<div align="center">
# 🚀 Telegram Downloader Bot

![Python](https://img.shields.io/badge/Python-3.9%2B-blue?logo=python)
![MongoDB](https://img.shields.io/badge/Database-MongoDB-47A248?logo=mongodb)
![Docker](https://img.shields.io/badge/Docker-Supported-2496ED?logo=docker)
![License](https://img.shields.io/badge/License-MIT-green)
![Maintained](https://img.shields.io/badge/Maintained-Yes-brightgreen)

A powerful, modular Telegram bot for downloading **public & private
Telegram content**\
with persistent queue management, encrypted sessions, and
enterprise‑ready architecture.


------------------------------------------------------------------------

## ✨ Why This Bot?

-   🔓 Access restricted/private content securely
-   📦 Batch download support (`100-200` ranges)
-   ⏸ Persistent queue system (restart-safe)
-   🔐 Encrypted session storage
-   📊 Real-time monitoring & metrics
-   🐳 Docker-ready deployment
-   🏗 Modular & scalable architecture

------------------------------------------------------------------------

# 🏗 Architecture Overview

    User Request
         ↓
    Handlers Layer (commands / callbacks / messages)
         ↓
    Service Layer (downloader / queue / session / uploader)
         ↓
    Security Layer (auth / encryption)
         ↓
    MongoDB Persistence
         ↓
    Telegram API (Pyrogram)

Designed for scalability, fault tolerance, and clean separation of
concerns.

------------------------------------------------------------------------

# 📁 Project Structure

``` bash
telegram-downloader/
│
├── backups/
├── downloads/
├── logs/
│
├── database/
│   ├── __init__.py
│   └── mongodb.py
│
├── plugins/
│   ├── core/
│   ├── handlers/
│   ├── monitoring/
│   ├── security/
│   ├── services/
│   └── progress_display.py
│
├── bot.py
├── config.py
└── requirements.txt
```

------------------------------------------------------------------------

# ⚡ Features

## 🔓 Restricted Content Access

Secure login system for private channels & groups.

## 📦 Smart Batch Processing

Download message ranges with queue management and progress tracking.

## ⏸ Persistent Queue Engine

Tasks survive restarts and resume automatically.

## 👑 Admin Dashboard

System stats, broadcast, backups, and user monitoring.

## 🔐 Security First

Encrypted sessions, rate limiting, and strict admin validation.

------------------------------------------------------------------------

# 🛠 Requirements

-   Python 3.9+
-   MongoDB 4.4+
-   Telegram API credentials (my.telegram.org)
-   Bot token (@BotFather)

------------------------------------------------------------------------

# 🚀 Quick Setup

``` bash
git clone https://github.com/yourusername/telegram-downloader.git
cd telegram-downloader

python -m venv venv
source venv/bin/activate

pip install -r requirements.txt
```

Create `.env`:

``` ini
API_ID=123456
API_HASH=your_api_hash
BOT_TOKEN=your_bot_token
MONGODB_URI=mongodb://localhost:27017
DATABASE_NAME=telegram_downloader
ADMINS=123456789
```

Run:

``` bash
python bot.py
```

------------------------------------------------------------------------

# 🐳 Docker Deployment

``` bash
docker-compose up -d
```

Production-ready with volume persistence for logs, downloads, and
backups.

------------------------------------------------------------------------

# 📖 Commands

## 👤 User

  Command     Description
  ----------- -----------------------
  /start      Initialize bot
  /login      Connect account
  /settings   Customize preferences
  /cancel     Stop active task
  /status     Bot health

## 👑 Admin

  Command      Description
  ------------ -------------------
  /stats       System metrics
  /users       List users
  /broadcast   Broadcast message
  /backup      Create backup

------------------------------------------------------------------------

# 📊 Monitoring & Maintenance

-   Automatic cleanup of stale tasks
-   Backup rotation
-   MongoDB health checks
-   Resource monitoring
-   Download success metrics

------------------------------------------------------------------------

# 🛡 Security Best Practices

-   Never commit `.env`
-   Encrypted session storage
-   Input validation
-   Admin-only protected commands
-   Rate limiting

------------------------------------------------------------------------

# 📝 License

MIT License

------------------------------------------------------------------------

<div align="center">
### 🌟 If you find this project useful:

Star the repository • Contribute • Share with others

Built for reliability. Designed for scale.
