import os
import json
import random
import asyncio
import time
import re
import datetime
import uuid as _uuid
import aiohttp
import firebase_admin
from firebase_admin import credentials, db
import discord
from discord import app_commands
from discord.ext import commands

class DailyCreditView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim 5 Credits", style=discord.ButtonStyle.danger, emoji="🎁", custom_id="daily_claim_btn")
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_daily_claim(interaction)

class StormBomberBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        self.add_view(DailyCreditView())
        await self.tree.sync()
        print("🔄 Email bot commands synced successfully!")

bot = StormBomberBot()

LOG_CHANNEL_ID = 1505635730289066036
ADMIN_ROLE_ID = 1499497341693202664
ALLOWED_USERS = [
    1483411120961093642,
    1493293951959044147,
    1130542850883469443,
]

active_spam_tasks: dict[int, asyncio.Task] = {}
tempRequests = {}
activeDrops = {}
firebase_ready = False

try:
    firebase_config = os.getenv("FIREBASE_CONFIG") or os.getenv("FIREBASE_SERVICE_ACCOUNT")
    if not firebase_config:
        raise RuntimeError("Missing FIREBASE_CONFIG or FIREBASE_SERVICE_ACCOUNT environment variable")

    firebase_config = json.loads(firebase_config)
    cred = credentials.Certificate(firebase_config)

    firebase_database_url = os.getenv("FIREBASE_DATABASE_URL")
    if not firebase_database_url:
        raise RuntimeError("Missing FIREBASE_DATABASE_URL environment variable")

    firebase_admin.initialize_app(cred, {
        'databaseURL': firebase_database_url
    })
    firebase_ready = True
    print("✅ Firebase initialized for email bot!")
except Exception as e:
    print("Firebase Error:", e)


class _DummyRef:
    def __init__(self, path=""):
        self._path = path.strip("/")
        self._file = os.getenv("FIREBASE_FALLBACK_FILE", "firebase_fallback.json")

    def _load(self):
        try:
            if not os.path.exists(self._file):
                return {}
            with open(self._file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save(self, data):
        try:
            with open(self._file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Firebase fallback save error: {e}")

    def _log_fallback(self, action: str, value=None):
        print(f"[Firebase Fallback] {action}: {self._path} -> {value}")

    def _get_by_path(self, data, path_parts):
        cur = data
        for p in path_parts:
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                return None
        return cur

    def _set_by_path(self, data, path_parts, value):
        cur = data
        for p in path_parts[:-1]:
            if p not in cur or not isinstance(cur[p], dict):
                cur[p] = {}
            cur = cur[p]
        cur[path_parts[-1]] = value

    def get(self):
        data = self._load()
        if not self._path:
            return data
        parts = self._path.split("/")
        return self._get_by_path(data, parts)

    def set(self, value):
        data = self._load()
        if not self._path:
            if isinstance(value, dict):
                data = value
            else:
                return
        else:
            parts = self._path.split("/")
            self._set_by_path(data, parts, value)
        self._save(data)
        self._log_fallback("set", value)

    def update(self, val: dict):
        if not isinstance(val, dict):
            return
        data = self._load()
        if not self._path:
            # merge at root
            for k, v in val.items():
                data[k] = v
        else:
            parts = self._path.split("/")
            cur = self._get_by_path(data, parts) or {}
            if not isinstance(cur, dict):
                cur = {}
            for k, v in val.items():
                cur[k] = v
            self._set_by_path(data, parts, cur)
        self._save(data)
        self._log_fallback("update", val)

    def delete(self):
        data = self._load()
        if not self._path:
            data.clear()
        else:
            parts = self._path.split("/")
            cur = data
            for p in parts[:-1]:
                if p not in cur or not isinstance(cur[p], dict):
                    return
                cur = cur[p]
            cur.pop(parts[-1], None)
        self._save(data)

    def child(self, name):
        new_path = f"{self._path}/{name}" if self._path else str(name)
        return _DummyRef(new_path)


def safe_db_ref(path: str):
    if firebase_ready:
        try:
            return db.reference(path)
        except Exception as e:
            print(f"Firebase reference error for {path}: {e}")
    return _DummyRef(path)


def is_manager(interaction: discord.Interaction) -> bool:
    if interaction.user.id in ALLOWED_USERS:
        return True
    member = interaction.user
    if interaction.guild and isinstance(member, discord.Member):
        return any(role.id == ADMIN_ROLE_ID for role in member.roles)
    return False

async def send_detailed_log(title, user, details, color=0x5865F2):
    try:
        ch = bot.get_channel(LOG_CHANNEL_ID) or await bot.fetch_channel(LOG_CHANNEL_ID)
        if not ch:
            return
        embed = discord.Embed(title=title, color=color)
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="👤 User:", value=f"{user.mention} (`{user.id}`)", inline=False)
        for detail in details:
            embed.add_field(name=detail["name"], value=detail["value"], inline=False)
        await ch.send(embed=embed)
    except Exception:
        pass


async def handle_daily_claim(interaction: discord.Interaction):
    user_ref = safe_db_ref(f"users/{interaction.user.id}")
    snap = user_ref.get()
    cur_credits = snap.get("credits", "0") if snap else "0"
    last_claim = snap.get("last_claim", 0) if snap else 0
    now_timestamp = int(time.time())
    cooldown_seconds = 24 * 3600

    if now_timestamp - last_claim < cooldown_seconds:
        remaining_seconds = cooldown_seconds - (now_timestamp - last_claim)
        hours, remainder = divmod(remaining_seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        cooldown_embed = discord.Embed(
            title="⏳ Already claimed today",
            description=f"Come back in **{hours}h {minutes}m**",
            color=discord.Color.from_rgb(47, 49, 54)
        )
        return await interaction.response.send_message(embed=cooldown_embed, ephemeral=True)

    if cur_credits != "lifetime":
        try:
            curr_int = int(cur_credits or 0)
        except ValueError:
            curr_int = 0
        new_credits = str(curr_int + 5)
        user_ref.update({"credits": new_credits, "last_claim": now_timestamp})
        balance_text = f"New balance: **{new_credits}** credits"
    else:
        user_ref.update({"last_claim": now_timestamp})
        balance_text = "Your balance is **Lifetime**"

    success_embed = discord.Embed(
        title="🎁 +5 Credits!",
        description=f"{balance_text}\nCome back tomorrow!",
        color=discord.Color.from_rgb(47, 49, 54)
    )
    await interaction.response.send_message(embed=success_embed, ephemeral=True)


def random_ua() -> str:
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
    )

async def _async_req(session, method, url, data=None, json_body=None, extra_headers=None, label=""):
    headers = {
        "User-Agent": random_ua(),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }
    if extra_headers:
        headers.update(extra_headers)

    if isinstance(data, str):
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")

    try:
        timeout = aiohttp.ClientTimeout(total=12)
        method_name = method.upper()
        if method_name == "POST":
            if json_body is not None:
                headers.setdefault("Content-Type", "application/json")
                async with session.post(url, json=json_body, headers=headers, timeout=timeout, ssl=False) as r:
                    await r.read()
                    ok = 200 <= r.status < 300
                    return ok, label, "OK" if ok else f"HTTP {r.status}"
            async with session.post(url, data=data, headers=headers, timeout=timeout, ssl=False) as r:
                await r.read()
                ok = 200 <= r.status < 300
                return ok, label, "OK" if ok else f"HTTP {r.status}"
        elif method_name == "PUT":
            if json_body is not None:
                headers.setdefault("Content-Type", "application/json")
                async with session.put(url, json=json_body, headers=headers, timeout=timeout, ssl=False) as r:
                    await r.read()
                    ok = 200 <= r.status < 300
                    return ok, label, "OK" if ok else f"HTTP {r.status}"
            async with session.put(url, data=data, headers=headers, timeout=timeout, ssl=False) as r:
                await r.read()
                ok = 200 <= r.status < 300
                return ok, label, "OK" if ok else f"HTTP {r.status}"
        else:
            async with session.get(url, headers=headers, timeout=timeout, ssl=False) as r:
                await r.read()
                ok = 200 <= r.status < 300
                return ok, label, "OK" if ok else f"HTTP {r.status}"
    except Exception as e:
        return False, label, str(type(e).__name__)


async def _preflight_page(session, url, extra_headers=None):
    headers = {
        "User-Agent": random_ua(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }
    if extra_headers:
        headers.update(extra_headers)

    try:
        timeout = aiohttp.ClientTimeout(total=12)
        async with session.get(url, headers=headers, timeout=timeout, ssl=False) as r:
            await r.read()
    except Exception:
        pass


async def _get_page_text(session, url, extra_headers=None):
    headers = {
        "User-Agent": random_ua(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }
    if extra_headers:
        headers.update(extra_headers)

    try:
        timeout = aiohttp.ClientTimeout(total=12)
        async with session.get(url, headers=headers, timeout=timeout, ssl=False) as r:
            return await r.text()
    except Exception:
        return None


def _extract_csrf_token(html: str) -> str | None:
    if not html:
        return None
    match = re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']', html)
    if match:
        return match.group(1)
    match = re.search(r'csrf_token["\']?\s*[:=]\s*["\']([^"\']+)["\']', html)
    if match:
        return match.group(1)
    return None


def normalize_email(email: str) -> str:
    return email.strip().lower()

async def _lidorbar_login(session, email):
    return await _async_req(
        session,
        "POST",
        "https://account.lidorbar.com/authentication/login",
        json_body={
            "email": email,
            "client_id": "f602bb9e-c1e8-469e-b2ef-5a7fd5fd5fba",
            "redirect_uri": "/authentication/oauth/authorize?buyer_flags=eyJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJsaWRvcmJhci1zdG9yZS5teXNob3BpZnkuY29tIiwiZmxhZ3MiOltdLCJleHAiOjE3ODI2MzcyMDEsIm5iZiI6MTc4MjAzMjQwMX0.mWWI98nUfxWHnGFxdK-3UKZCLiRzae2e_wWaaUMJUWY&client_id=f602bb9e-c1e8-469e-b2ef-5a7fd5fd5fba&locale=he-IL&nonce=1569f9ab-f8ec-4a0a-83d6-77ba854b47e9&redirect_uri=https%3A%2F%2Faccount.lidorbar.com%2Fcallback&region_country=IL&response_type=code&scope=openid+email+customer-account-api%3Afull&state=hWNDaqt0GO7bzQWUwO4WQDPF",
            "region_country": "IL",
            "step": "email",
        },
        extra_headers={
            "accept": "application/json",
            "accept-language": "he-IL",
            "content-type": "application/json; charset=UTF-8",
            "origin": "https://account.lidorbar.com",
            "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
            "sec-ch-ua-mobile": "?1",
            "sec-ch-ua-platform": '"Android"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Mobile Safari/537.36",
            "x-csrf-token": os.getenv("LIDORBAR_CSRF_TOKEN", ""),
        },
        label="lidorbar",
    )

# (file continues...)
