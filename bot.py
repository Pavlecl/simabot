import os
import re
import subprocess
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# КОНФИГУРАЦИЯ
BOT_TOKEN = "8655677245:AAGXnRlWJNL-jur7VR1872kjGCjjpj0MFEk"
ALLOWED_USER_ID = 120952174

# Мапа кодов стран на русские названия и флаги
COUNTRIES = {
    'RU': ('Россия', '🇷🇺'), 'CN': ('Китай', '🇨🇳'), 'US': ('США', '🇺🇸'),
    'NL': ('Нидерланды', '🇳🇱'), 'GB': ('Великобритания', '🇬🇧'),
    'DE': ('Германия', '🇩🇪'), 'FR': ('Франция', '🇫🇷'),
    'LT': ('Литва', '🇱🇹'), 'UA': ('Украина', '🇺🇦'),
    'KZ': ('Казахстан', '🇰🇿'), 'BY': ('Беларусь', '🇧🇾'),
    'PL': ('Польша', '🇵🇱'), 'JP': ('Япония', '🇯🇵'),
    'KR': ('Южная Корея', '🇰🇷'), 'IN': ('Индия', '🇮🇳'),
    'BR': ('Бразилия', '🇧🇷'), 'AU': ('Австралия', '🇦🇺'),
    'CA': ('Канада', '🇨🇦'), 'IT': ('Италия', '🇮🇹'),
    'ES': ('Испания', '🇪🇸'), 'TR': ('Турция', '🇹🇷'),
    'VN': ('Вьетнам', '🇻🇳'), 'TH': ('Таиланд', '🇹🇭'),
    'SG': ('Сингапур', '🇸🇬'), 'AE': ('ОАЭ', '🇦🇪')
}

def get_country_info(country_code):
    """Получить название страны и флаг"""
    if country_code in COUNTRIES:
        name, flag = COUNTRIES[country_code]
        return f"{flag} {name}"
    return f"🌍 {country_code}"

def get_ip_country(ip):
    """Получить код страны для IP"""
    try:
        result = subprocess.run(['whois', ip], capture_output=True, text=True, timeout=5)
        for line in result.stdout.split('\n'):
            if line.startswith('country:'):
                return line.split(':')[1].strip()
    except:
        pass
    return None

def is_authorized(user_id: int) -> bool:
    return user_id == ALLOWED_USER_ID

def get_main_keyboard():
    """Главное меню с кнопками"""
    keyboard = [
        [
            InlineKeyboardButton("📊 Статистика", callback_data='status'),
            InlineKeyboardButton("🖥 Сервер", callback_data='server')
        ],
        [
            InlineKeyboardButton("🚨 Атаки", callback_data='attacks'),
            InlineKeyboardButton("🔴 Баны", callback_data='bans')
        ],
        [
            InlineKeyboardButton("🔄 Обновить", callback_data='refresh')
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("⛔ У вас нет доступа к этому боту")
        return
    
    await update.message.reply_text(
        "🤖 <b>Golem Security Bot</b>\n\nВыберите действие:",
        parse_mode='HTML',
        reply_markup=get_main_keyboard()
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if not is_authorized(query.from_user.id):
        return
    
    action = query.data
    
    if action == 'status':
        await show_status(query)
    elif action == 'server':
        await show_server(query)
    elif action == 'attacks':
        await show_attacks(query)
    elif action == 'bans':
        await show_bans(query)
    elif action == 'refresh':
        await query.edit_message_text(
            "🤖 <b>Golem Security Bot</b>\n\nВыберите действие:",
            parse_mode='HTML',
            reply_markup=get_main_keyboard()
        )

async def show_status(query):
    """Статистика Fail2ban"""
    try:
        result = subprocess.run(['sudo', 'fail2ban-client', 'status', 'sshd'], capture_output=True, text=True)
        
        output = result.stdout
        currently_failed = 0
        total_failed = 0
        currently_banned = 0
        total_banned = 0
        
        for line in output.split('\n'):
            if 'Currently failed' in line:
                currently_failed = int(line.split(':')[1].strip())
            elif 'Total failed' in line:
                total_failed = int(line.split(':')[1].strip())
            elif 'Currently banned' in line:
                currently_banned = int(line.split(':')[1].strip())
            elif 'Total banned' in line:
                total_banned = int(line.split(':')[1].strip())
        
        message = (
            f"📊 <b>Fail2ban Statistics</b>\n\n"
            f"<b>SSH Protection:</b>\n"
            f"• Currently failed: <b>{currently_failed}</b>\n"
            f"• Total failed: <b>{total_failed}</b>\n"
            f"• Currently banned: <b>{currently_banned}</b> 🔴\n"
            f"• Total banned: <b>{total_banned}</b>\n\n"
            f"🕐 {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
        )
        
        await query.edit_message_text(message, parse_mode='HTML', reply_markup=get_main_keyboard())
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {str(e)}", reply_markup=get_main_keyboard())

async def show_attacks(query):
    """Последние атаки с странами"""
    try:
        result = subprocess.run(['sudo', 'grep', 'Failed password', '/var/log/auth.log'], capture_output=True, text=True)
        lines = result.stdout.strip().split('\n')[-15:]
        
        ip_attacks = {}
        for line in lines:
            match = re.search(r'from (\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', line)
            if match:
                ip = match.group(1)
                ip_attacks[ip] = ip_attacks.get(ip, 0) + 1
        
        message = "🚨 <b>Recent Attacks (Last 15):</b>\n\n"
        
        country_stats = {}
        for ip, count in ip_attacks.items():
            country_code = get_ip_country(ip)
            country_info = get_country_info(country_code) if country_code else "🌍 Unknown"
            
            if country_info not in country_stats:
                country_stats[country_info] = []
            country_stats[country_info].append((ip, count))
        
        for country, ips in sorted(country_stats.items()):
            message += f"\n<b>{country}:</b>\n"
            for ip, count in ips:
                message += f"  • <code>{ip}</code> ({count}x)\n"
        
        message += f"\n🕐 {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
        
        await query.edit_message_text(message, parse_mode='HTML', reply_markup=get_main_keyboard())
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {str(e)}", reply_markup=get_main_keyboard())

async def show_bans(query):
    """Забаненные IP с странами"""
    try:
        result = subprocess.run(['sudo', 'fail2ban-client', 'status', 'sshd'], capture_output=True, text=True)
        
        output = result.stdout
        banned_ips = []
        
        for line in output.split('\n'):
            if 'Banned IP list' in line:
                ips_str = line.split(':')[1].strip()
                if ips_str:
                    banned_ips = ips_str.split()
        
        if not banned_ips:
            await query.edit_message_text("✅ Нет забаненных IP", reply_markup=get_main_keyboard())
            return
        
        message = f"🔴 <b>Banned IPs ({len(banned_ips)}):</b>\n\n"
        
        country_groups = {}
        for ip in banned_ips:
            country_code = get_ip_country(ip)
            country_info = get_country_info(country_code) if country_code else "🌍 Unknown"
            
            if country_info not in country_groups:
                country_groups[country_info] = []
            country_groups[country_info].append(ip)
        
        for country, ips in sorted(country_groups.items()):
            message += f"\n<b>{country}:</b>\n"
            for ip in ips:
                message += f"  • <code>{ip}</code>\n"
        
        message += f"\n🕐 {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
        
        await query.edit_message_text(message, parse_mode='HTML', reply_markup=get_main_keyboard())
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {str(e)}", reply_markup=get_main_keyboard())

async def show_server(query):
    """Статус сервера с температурой"""
    try:
        cpu_result = subprocess.run(['bash', '-c', "top -bn1 | grep 'Cpu(s)' | awk '{print $2}'"], capture_output=True, text=True)
        cpu_usage = cpu_result.stdout.strip().replace('%us,', '')
        
        mem_result = subprocess.run(['free', '-h'], capture_output=True, text=True)
        mem_lines = mem_result.stdout.split('\n')[1].split()
        mem_total = mem_lines[1]
        mem_used = mem_lines[2]
        
        disk_result = subprocess.run(['df', '-h', '/'], capture_output=True, text=True)
        disk_line = disk_result.stdout.split('\n')[1].split()
        disk_total = disk_line[1]
        disk_used = disk_line[2]
        disk_percent = disk_line[4]
        
        temp = "N/A"
        try:
            temp_result = subprocess.run(['bash', '-c', "cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | head -1"], capture_output=True, text=True)
            if temp_result.stdout.strip():
                temp_c = int(temp_result.stdout.strip()) / 1000
                temp = f"{temp_c:.1f}°C"
        except:
            pass
        
        uptime_result = subprocess.run(['uptime', '-p'], capture_output=True, text=True)
        uptime = uptime_result.stdout.strip().replace('up ', '')
        
        message = (
            f"🖥 <b>Server Status - Golem</b>\n\n"
            f"<b>💻 CPU:</b> {cpu_usage}%\n"
            f"<b>🌡 Temperature:</b> {temp}\n"
            f"<b>🧠 RAM:</b> {mem_used} / {mem_total}\n"
            f"<b>💾 Disk:</b> {disk_used} / {disk_total} ({disk_percent})\n"
            f"<b>⏱ Uptime:</b> {uptime}\n\n"
            f"🕐 {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
        )
        
        await query.edit_message_text(message, parse_mode='HTML', reply_markup=get_main_keyboard())
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {str(e)}", reply_markup=get_main_keyboard())

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    print("🤖 Bot started with buttons...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()