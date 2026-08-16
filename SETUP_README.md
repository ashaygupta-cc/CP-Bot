# CP-Bot v5 — Complete Setup Guide
### Binary Beats Discord Server

---

## Pehle samjho — Kya kya add hua hai v5 mein?

Tumhara existing CP-Bot (submission checker, points, leaderboard) bilkul waise hi kaam karta rahega. Usme koi change nahi hua. Sirf **2 naye features** add hue hain:

| Feature | Kya karta hai | Kaunsi file |
|---|---|---|
| **Verification System** | Jab koi member server join kare, usse pehle LinkedIn follow karna padega tabhi server access milega | `cogs/verification.py` |
| **Inactivity Tracker** | Jo member 15/20/25 din se koi problem solve nahi karta, use warnings milti hain. 30 din pe auto-kick | `cogs/inactivity.py` |

Aur `config.py` aur `bot.py` update hue hain sirf inhe support karne ke liye.

---

## PART 1: Files apne GitHub Repo mein daalo

### Kya replace karna hai, kya naya add karna hai:

```
cp-bot/                          STATUS
├── bot.py                   ←  REPLACE karo (2 nayi lines add hain)
├── config.py                ←  REPLACE karo (nayi env variables hain)
└── cogs/
    ├── verification.py      ←  NAYA FILE hai, abhi exist nahi karta
    └── inactivity.py        ←  NAYA FILE hai, abhi exist nahi karta
```

**Baki sab files bilkul mat chhedna:**
`checker.py`, `leaderboard.py`, `points.py`, `admin.py`, `problems.py`,
`registry.py`, `reset.py`, `submissions.py`, `queries.py`, `schema.sql`,
`connection.py` — ye sab unchanged hain, inhe touch nahi karna.

---

## PART 2: Discord Server mein Setup karo

Ye sab Discord ke andar karna hai, code se pehle.

---

### Step 2.1 — 2 Naye Roles Banao

Discord Server → Server Settings (gear icon) → Roles → "+ Create Role" pe click karo.

---

**Role #1 — `Verification`**

Ye role sirf ek kaam karta hai: jab koi naya member join kare, use ye role milta hai
aur usse puri server ka access nahi hota jab tak wo LinkedIn follow na kare.

- **Name:** `Verification` *(exactly aisa likhna — capital V, baki small)*
- **Color:** Red ya Orange (taaki admins ko pata chale ye pending hai)
- **Permissions:** Is role ko koi permissions mat dena — sab OFF raho
- **Position:** Is role ki koi special position zaruri nahi — bas exist karna chahiye

---

**Role #2 — `Member`**

Ye role milta hai jab member LinkedIn follow karke verify ho jaata hai.
Ye role hi decide karta hai ki member server ke channels dekh sakta hai ya nahi.

- **Name:** `Member` *(exactly aisa — capital M)*
- **Color:** Blue ya Green (verified member ka sign)
- **Permissions:** Normal member jaisi permissions — Read messages, Send messages, etc.
- **Position:** `@everyone` se upar hona chahiye

---

**IMPORTANT — Bot Role ki Position:**

```
Server Settings → Roles

Hierarchy kuch aisi honi chahiye:
┌─────────────────────────┐
│  Admin                  │  ← sabse upar
│  CP-Bot                 │  ← BOT KA ROLE YAHAN HONA CHAHIYE
│  Member                 │
│  Verification           │
│  @everyone              │  ← sabse neeche
└─────────────────────────┘
```

CP-Bot ka role `Member` aur `Verification` dono se **UPAR** hona chahiye.
Warna bot "Missing Permissions" error dega aur roles assign/remove nahi kar payega.

Role ko drag karke upar le jaao → Save Changes.

---

### Step 2.2 — Channels Setup

Tumhare server mein ye channels hone chahiye. Kuch already honge, kuch naye banane padenge.

| Channel | Kya karna hai | Status |
|---|---|---|
| `#verification` | Verification buttons yahan aate hain | Naya banao agar nahi hai |
| `#welcome` | Verified member ka welcome message yahan aata hai + inactivity warnings bhi | Pehle se hai ya banao |

**`#welcome` channel ke baare mein:** Tum ne bataya hai ki tumhare server mein `#general` nahi `#welcome` hai. Isliye `.env` mein hum `WELCOME_CHANNEL=welcome` likhenge. Inactivity ke public warnings bhi isi channel mein jaayenge.

---

### Step 2.3 — Channel Permissions Sahi Karo

Ye SABSE important step hai. Agar ye galat hua toh verification ka koi matlab nahi.

---

**`#verification` Channel ki Permissions:**

Is channel mein jaao → Settings (gear) → Permissions

```
@everyone
  ✅ View Channel       ON  (join karte hi dikhe)
  ❌ Send Messages      OFF (members khud type na kar paayein)
  ❌ Add Reactions      OFF

Verification role
  ✅ View Channel       ON  (pending members dikhe)
  ❌ Send Messages      OFF

Member role
  ❌ View Channel       OFF (verified members ko ye channel dikhne ki zarurat nahi)

CP-Bot (ya @bot role)
  ✅ View Channel       ON
  ✅ Send Messages      ON
  ✅ Embed Links        ON
  ✅ Manage Messages    ON
```

---

**Baaki SAARE channels ki Permissions (general, cp-discussion, etc.):**

Har channel mein ya Category level pe:

```
Verification role
  ❌ View Channel       OFF  ← pending members ko kuch nahi dikhna chahiye

Member role
  ✅ View Channel       ON   ← verified members sab dekh saken
  ✅ Send Messages      ON
```

> **Shortcut:** Agar tumhare channels categories mein hain, toh Category pe permission set karo,
> sab channels automatically inherit kar lenge.

---

**`#welcome` Channel ki Permissions:**

```
@everyone
  ✅ View Channel       ON   (sab dekh saken — welcome channel public hai)
  ❌ Send Messages      OFF  (sirf bot bheje)

Verification role
  ✅ View Channel       ON   (naye members bhi dekh saken welcome messages)

Member role
  ✅ View Channel       ON
  ✅ Send Messages      ON   (members comment kar saken welcome pe)

CP-Bot
  ✅ View Channel       ON
  ✅ Send Messages      ON
  ✅ Embed Links        ON
```

---

### Step 2.4 — Bot Permissions (Discord Developer Portal)

[discord.com/developers/applications](https://discord.com/developers/applications) pe jaao → Apna bot select karo → **Bot** tab

**Privileged Gateway Intents** section mein ye dono ON karo:

```
✅ SERVER MEMBERS INTENT    ← on_member_join ke liye zaroori
✅ MESSAGE CONTENT INTENT   ← commands ke liye zaroori
```

Save karo.

---

**Server mein Bot Permissions:**

Agar bot invite karte waqt ye permissions nahi diye the, toh Server Settings → Integrations → CP-Bot → Permissions mein add karo:

```
✅ View Channels
✅ Send Messages
✅ Read Message History
✅ Embed Links
✅ Use External Emojis
✅ Manage Roles          ← verification/member role assign karne ke liye
✅ Kick Members          ← 30 day inactivity pe kick karne ke liye
```

---

## PART 3: .env File Update karo

Apni `.env` file kholo (Render pe ho toh Dashboard → Environment Variables).

Purani variables waise hi rakhni hain. Sirf **nayi lines ADD karo** neeche:

```env
# ════════════════════════════════════════════
# EXISTING VARIABLES — CHANGE MAT KARO
# ════════════════════════════════════════════
DISCORD_TOKEN=your_token_here
DATABASE_URL=your_supabase_url_here
ADMIN_ROLE=Admin
PREFIX=!
RENDER_URL=https://your-bot-name.onrender.com
PORT=10000

# ════════════════════════════════════════════
# NEW — Verification System
# ════════════════════════════════════════════

# Channel ka naam jahan verification message aata hai (# mat lagao)
VERIFICATION_CHANNEL=verification

# Channel ka naam jahan welcome message aata hai verified hone ke baad
# Tumhare server mein #welcome hai, isliye "welcome" likho
WELCOME_CHANNEL=welcome

# Tumhara LinkedIn URL — member isko follow karenge
# Company page: https://www.linkedin.com/company/binary-beats
# Personal profile: https://www.linkedin.com/in/ashaygupta
LINKEDIN_URL=https://www.linkedin.com/in/ashaygupta

# Role ka naam jo naye member ko milta hai join hone pe (# nahi, # mat lagao)
VERIFICATION_ROLE=Verification

# Role ka naam jo verify hone ke baad milta hai
MEMBER_ROLE=Member

# ════════════════════════════════════════════
# NEW — Inactivity Tracker
# ════════════════════════════════════════════

# Channel jahan public warnings + kick notifications jaate hain
# Tumhare server mein #welcome hai, wahan jaayega
INACTIVITY_CHANNEL=welcome

# true = 30 din ke baad auto kick hoga
# false = sirf warnings, kick nahi (test karte waqt false karo)
INACTIVITY_KICK_ENABLED=true

# ════════════════════════════════════════════
# OPTIONAL — Nightly Check Summary
# ════════════════════════════════════════════
# Agar chahte ho ki raat 23:58 wala auto-checkall ka summary
# kisi channel mein aaye, toh us channel ka ID yahan daalo.
# Channel ID kaise milega: channel pe right click → Copy Channel ID
# CHECKALL_CHANNEL_ID=123456789012345678
```

> **Note:** `.env` file mein spaces mat daalo `=` ke aage peeche.
> `WELCOME_CHANNEL = welcome` galat hai, `WELCOME_CHANNEL=welcome` sahi hai.

---

## PART 4: Render pe Deploy karo

1. GitHub pe saari nayi files push karo:
   ```bash
   git add .
   git commit -m "v5: add verification and inactivity system"
   git push
   ```

2. Render Dashboard pe jaao → Apna service select karo → **Environment** tab

3. Ye nayi variables add karo (ek ek karke):
   - `VERIFICATION_CHANNEL` = `verification`
   - `WELCOME_CHANNEL` = `welcome`
   - `LINKEDIN_URL` = tumhara actual LinkedIn URL
   - `VERIFICATION_ROLE` = `Verification`
   - `MEMBER_ROLE` = `Member`
   - `INACTIVITY_CHANNEL` = `welcome`
   - `INACTIVITY_KICK_ENABLED` = `true`

4. **Manual Deploy** trigger karo: Render → Deployments → Deploy Latest Commit

5. Logs mein dekho — ye lines aani chahiye:
   ```
   ✅  Database connected.
      ✓  Loaded cogs.verification
      ✓  Loaded cogs.inactivity
   ✅  Inactivity daily check task started (09:00 IST).
   ```

---

## PART 5: Bot Start hone ke baad — First Time Setup

---

### Step 5.1 — Verification Message Post Karo

`#verification` channel mein jaao aur type karo:
```
!sendverification
```

Bot ek embed post karega jisme 2 buttons honge:
- `Follow on LinkedIn 🔗` — click karne pe LinkedIn page khulega
- `I Have Followed ✅` — click karne pe verify ho jaayenge

Ye message **permanent** hai — bot restart hone ke baad bhi button kaam karta rahega.

---

### Step 5.2 — Existing Members ko Verify Karo

Tumhare server mein jo members pehle se hain, unhe bhi LinkedIn follow karne ke liye bolna hai.
Iske liye ek hi command hai:

```
!verifyall
```

Ye command:
- Server ke **har member** ko check karta hai jo abhi `Member` role ke bina hai
- Unhe automatically `Verification` role assign karta hai
- Har member ko **DM** bhejta hai jisme likha hota hai ki `#verification` mein jaao
  aur LinkedIn follow karo
- Agar kisi ka DM band hai, toh woh `#verification` channel pe jaake khud verify kar
  sakta hai (message wahan already hoga `!sendverification` se)
- End mein admin ko report deta hai — kitne DMs gaye, kitne failed

---

### Step 5.3 — Inactivity Check Test Karo

Pehle dekho ki kaun kaun inactive hai:
```
!inactivity
```

Bot color-coded report dega:
- 🔴 Red = 25+ din inactive (kick ke kareeb)
- 🟠 Orange = 20-24 din inactive
- 🟡 Yellow = 15-19 din inactive
- 🟢 Green = 15 din se kam (active)

Abhi manually trigger karna ho toh:
```
!inactivitycheck
```

Ye abhi warnings/kicks fire karega as per thresholds. Normal usage mein ye automatically
roz 09:00 IST pe hota hai.

---

### Step 5.4 — Test karo ki sab kaam kar raha hai

**Verification test:**
1. Server pe ek doosre account se join karo (ya kisi dost se join karwao)
2. Unhe sirf `#verification` channel dikhna chahiye — aur kuch nahi
3. `#verification` mein bot ka message aur dono buttons honge
4. "Follow on LinkedIn" click karo — LinkedIn page khuleg
5. Wapas aao, "I Have Followed ✅" click karo
6. Verification role hatna chahiye, Member role milna chahiye
7. `#welcome` mein welcome embed aana chahiye

**Inactivity test (optional):**
```
!exemptinactivity @tumhara_account
```
Phir `!inactivitycheck` chalao — tumhara account skip hoga, baaki sab check honge.

---

## Poora Flow Samjho

### Verification Flow:

```
Naya member server join karta hai
            │
            ▼
Bot automatically "Verification" role assign karta hai
Bot #verification mein embed post karta hai member ko mention karke:
  ┌─────────────────────────────────────────┐
  │  🔐 Verify to Join the Server           │
  │                                         │
  │  [Follow on LinkedIn 🔗] [I Have Followed ✅] │
  └─────────────────────────────────────────┘
            │
            ▼
Member "Follow on LinkedIn 🔗" click karta hai
→ LinkedIn page browser mein khulta hai
→ Member follow karta hai
            │
            ▼
Member wapas Discord pe aata hai
"I Have Followed ✅" button click karta hai
            │
            ▼
Bot check karta hai:
  - Agar already Member role hai → "Already verified!" message (sirf usse dikhta hai)
  - Agar nahi hai → roles update karta hai
            │
            ▼
Bot "Verification" role REMOVE karta hai
Bot "Member" role ADD karta hai
Member ko poora server access mil jaata hai
            │
            ▼
#welcome mein public welcome embed aata hai:
  "🎉 Welcome @member to Binary Beats! 🏆
   They've followed us on LinkedIn and are ready to grind! 💪"
```

---

### Inactivity Flow:

```
Roz 09:00 IST pe bot automatically check karta hai
            │
            ▼
Har registered member ka last solve check hota hai
(registered = jisne !register cf/lc/cc karke handle link kiya ho)
            │
            ├── Last solve 15-19 din pehle?
            │     └── DM bhejta hai: "Hey! 15 din ho gaye, kuch solve karo"
            │
            ├── Last solve 20-24 din pehle?
            │     └── DM bhejta hai: "Warning #2 — X din mein kick ho jaoge"
            │
            ├── Last solve 25-29 din pehle?
            │     ├── DM bhejta hai: "FINAL WARNING — X din mein kick"
            │     └── #welcome mein public mention karta hai
            │
            └── Last solve 30+ din pehle (ya kabhi solve nahi kiya)?
                  ├── Member ko DM karta hai: "Tumhe remove kiya ja raha hai"
                  ├── Member ko kick karta hai
                  └── #welcome mein kick log embed post karta hai
```

**Activity kya count hoti hai?**
Sirf `!check` ya `!checkall` ke baad jo solves DB mein record hote hain — wahi activity hai.
Discord pe online rehna ya message karna activity nahi count hota.

**Kisi ko exempt karna hai temporarily?**
```
!exemptinactivity @username
```
Woh member skip hoga inactivity check mein.
*(Note: Bot restart pe exemption reset hoti hai — permanent ke liye README ke end mein SQL dekho)*

---

## Admin Commands — Quick Reference Card

### Verification Commands
```
!sendverification
    → #verification mein verification embed + buttons post karta hai
    → Pehli baar setup ke baad chalao, ya agar message delete ho jaaye

!verifyall
    → Server ke SAARE unverified members ko DM karta hai
    → Unhe Verification role assign karta hai
    → Pehli baar run karo existing members ke liye
    → Safe hai multiple times chalane pe

!reverify @username
    → Kisi specific member ko wapas verification pe bhejta hai
    → Member role remove karta hai, Verification role deta hai
    → #verification mein unhe mention karta hai
    → Use case: kisi ne LinkedIn unfollow kiya to reverify karo

!verificationstatus
    → Batata hai kitne members abhi pending hain (Verification role ke saath)
    → Kitne verified hain (Member role ke saath)
```

### Inactivity Commands
```
!inactivity
    → Full color-coded report — har member ka last solve date aur days count
    → 🔴 25+ din, 🟠 20-24 din, 🟡 15-19 din, 🟢 active

!inactivitycheck
    → ABHI warnings aur kicks fire karo — daily 09:00 IST ka wait mat karo
    → Test karne ke liye useful

!exemptinactivity @username
    → Is member ko inactivity check se permanently skip karo
    → Admins, active contributors ke liye use karo
    → Bot restart pe reset ho jaata hai

!unexemptinactivity @username
    → Exemption hatao, member phir se normal check mein aayega
```

---

## Troubleshooting

### "Role 'Member' not found" ya "Role 'Verification' not found" error
**Cause:** `.env` mein jo naam likha hai woh server ke role naam se bilkul match nahi karta.
**Fix:** Server Settings → Roles mein exact naam dekho. Case-sensitive hai — `member` aur `Member` alag hain.
Phir `.env` update karo: `MEMBER_ROLE=Member` (exactly jaisa server mein hai).

---

### Bot role assign/remove nahi kar pa raha — "Missing Permissions" error
**Cause:** Bot ka role hierarchy mein Member/Verification role se neeche hai.
**Fix:** Server Settings → Roles → CP-Bot ka role drag karke Member aur Verification dono se UPAR le jaao → Save.

---

### Verification button click karne pe kuch nahi hota
**Cause 1:** Bot offline hai ya restart hua — persistent view re-register nahi hui.
**Fix:** Bot ko restart karo. Phir se try karo button.

**Cause 2:** Bot ke paas Manage Roles permission nahi hai.
**Fix:** Server Settings → Integrations → CP-Bot → Manage Roles ON karo.

---

### Naye member ko `#verification` ke alawa aur channels dikh rahe hain
**Cause:** Verification role ko us channel mein "View Channel: OFF" set nahi kiya.
**Fix:** Har channel (ya category) pe jaao → Permissions → Verification role → View Channel: OFF.

---

### `!verifyall` ne DMs send ki lekin members ke paas DMs band hain
**Cause:** Discord users apne DMs band kar sakte hain.
**Fix:** Ye normal hai. Bot report mein "DMs failed" count dikhata hai.
In members ke liye `#verification` channel ka message enough hai — woh wahan jaake verify kar sakte hain.

---

### Inactivity check chal nahi raha / warnings nahi aa rahi
**Cause 1:** Member ne `!register` se apna handle link nahi kiya — unregistered members check nahi hote.
**Fix:** Members ko `!register cf theirhandle` karne ko kaho.

**Cause 2:** Bot recently restart hua aur 09:00 IST abhi tak nahi aaya.
**Fix:** `!inactivitycheck` manually chalao.

**Cause 3:** Render pe environment variables set nahi hain.
**Fix:** Render Dashboard → Environment mein check karo.

---

### Bot kick nahi kar pa raha
**Cause:** "Kick Members" permission bot ke paas nahi hai.
**Fix:** Server Settings → Integrations → CP-Bot → Kick Members: ON.

---

### `#welcome` mein messages nahi aa rahe (welcome ya inactivity warnings)
**Cause:** `.env` mein `WELCOME_CHANNEL` aur `INACTIVITY_CHANNEL` galat set hai.
**Fix:** Dono ko `welcome` set karo (without #):
```
WELCOME_CHANNEL=welcome
INACTIVITY_CHANNEL=welcome
```
Render pe update karo aur redeploy karo.

---

## Optional: Permanent Inactivity Exemptions (Database mein store karo)

Abhi exemptions in-memory hain — bot restart hone pe reset ho jaati hain.
Agar permanent chahiye, toh Supabase SQL Editor mein ye run karo:

```sql
CREATE TABLE IF NOT EXISTS inactivity_exemptions (
    discord_id  TEXT NOT NULL,
    guild_id    TEXT NOT NULL,
    exempted_by TEXT NOT NULL,
    reason      TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (discord_id, guild_id)
);
```

Phir `cogs/inactivity.py` mein `__init__` mein DB se exemptions load karo aur
`!exemptinactivity` command mein DB mein save karo. Yeh v6 enhancement hai.

---

## Complete File Structure

```
cp-bot/
│
├── bot.py                ← v5 UPDATED — verification + inactivity cogs add hue
├── config.py             ← v5 UPDATED — nayi env variables add huin
├── keep_alive.py            unchanged
├── ping.py                  unchanged
├── render.yaml              unchanged
├── requirements.txt         unchanged
│
├── cogs/
│   ├── admin.py             unchanged
│   ├── checker.py           unchanged
│   ├── leaderboard.py       unchanged
│   ├── points.py            unchanged
│   ├── problems.py          unchanged
│   ├── registry.py          unchanged
│   ├── reset.py             unchanged
│   ├── submissions.py       unchanged
│   ├── verification.py   ← v5 NEW — LinkedIn verification system
│   └── inactivity.py     ← v5 NEW — 15/20/25 day warnings + 30 day kick
│
├── database/
│   ├── connection.py        unchanged
│   ├── queries.py           unchanged
│   └── schema.sql           unchanged — no new tables needed
│
└── platforms/
    ├── base.py              unchanged
    ├── atcoder.py           unchanged
    ├── codechef.py          unchanged
    ├── codeforces.py        unchanged
    └── leetcode.py          unchanged
```

---

## Quick Checklist — Sab kar liya?

```
Discord Server Setup:
  [ ] "Verification" role banaya
  [ ] "Member" role banaya
  [ ] CP-Bot role dono se upar hai hierarchy mein
  [ ] #verification channel banaya
  [ ] #verification channel permissions set ki (Verification role = view only, Member role = OFF)
  [ ] Baaki channels mein Verification role = View Channel: OFF
  [ ] Server Members Intent ON hai Discord Dev Portal mein
  [ ] Message Content Intent ON hai Discord Dev Portal mein
  [ ] Bot ke paas Manage Roles permission hai
  [ ] Bot ke paas Kick Members permission hai

.env / Render Environment Variables:
  [ ] VERIFICATION_CHANNEL=verification
  [ ] WELCOME_CHANNEL=welcome
  [ ] LINKEDIN_URL=tumhara actual URL
  [ ] VERIFICATION_ROLE=Verification
  [ ] MEMBER_ROLE=Member
  [ ] INACTIVITY_CHANNEL=welcome
  [ ] INACTIVITY_KICK_ENABLED=true

Files:
  [ ] bot.py replace kiya
  [ ] config.py replace kiya
  [ ] cogs/verification.py add kiya
  [ ] cogs/inactivity.py add kiya
  [ ] GitHub pe push kiya
  [ ] Render pe deploy kiya

First-time Commands:
  [ ] !sendverification → #verification mein chalaya
  [ ] !verifyall → existing members ko DM bheja
  [ ] !inactivity → report check ki
```

---

*CP-Bot v5 — Binary Beats Discord Server*
*Made with ❤️ for competitive programmers*
