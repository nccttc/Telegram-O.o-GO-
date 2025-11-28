# forwarder_bot_v7.py - 全面重构版本
import random
import logging
import configparser
import json
import os
from datetime import datetime
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    filters, ContextTypes, CallbackQueryHandler
)
from telegram.constants import ParseMode
from telegram.error import TelegramError

# ==================== 配置区 ====================
CONFIG_FILE = 'config.ini'
DATA_DIR = 'data'
MAPPING_FILE = os.path.join(DATA_DIR, 'user_mapping.json')
WHITELIST_FILE = os.path.join(DATA_DIR, 'whitelist.json')
BLACKLIST_FILE = os.path.join(DATA_DIR, 'blacklist.json')
STATS_FILE = os.path.join(DATA_DIR, 'statistics.json')
PENDING_VERIFY_FILE = os.path.join(DATA_DIR, 'pending_verify.json')

MAX_FAIL_LIMIT = 3
BOT_VERSION = "7.0"

# ==================== 日志配置 ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# ==================== 数据管理类 ====================
class DataManager:
    """统一数据持久化管理"""
    
    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        self.owner_id = 0
        self.bot_token = ""
        
        # 内存数据
        self.user_mapping = {}      # 消息ID -> 用户ID
        self.whitelist = set()      # 白名单
        self.blacklist = set()      # 黑名单
        self.pending_verify = {}    # 待验证: {user_id: {"answer": int, "attempts": int}}
        self.statistics = {         # 统计数据
            "total_messages": 0,
            "total_replies": 0,
            "blocked_attempts": 0,
            "verified_users": 0,
            "start_time": None
        }
        
    def load_all(self):
        """加载所有配置和数据"""
        self._load_config()
        self._load_json(MAPPING_FILE, 'user_mapping', key_type=int)
        self._load_json(WHITELIST_FILE, 'whitelist', as_set=True)
        self._load_json(BLACKLIST_FILE, 'blacklist', as_set=True)
        self._load_json(PENDING_VERIFY_FILE, 'pending_verify', key_type=int)
        self._load_json(STATS_FILE, 'statistics')
        
        if self.statistics.get("start_time") is None:
            self.statistics["start_time"] = datetime.now().isoformat()
            
        logger.info(f"数据加载完成: {len(self.user_mapping)}条映射, "
                   f"{len(self.whitelist)}白名单, {len(self.blacklist)}黑名单")
    
    def _load_config(self):
        """加载配置文件"""
        if not os.path.exists(CONFIG_FILE):
            logger.critical(f"配置文件 {CONFIG_FILE} 未找到！")
            exit(1)
        try:
            config = configparser.ConfigParser()
            config.read(CONFIG_FILE, encoding='utf-8')
            self.bot_token = config['Telegram']['BOT_TOKEN']
            self.owner_id = int(config['Telegram']['OWNER_ID'])
        except (KeyError, ValueError) as e:
            logger.critical(f"配置文件格式错误: {e}")
            exit(1)
    
    def _load_json(self, filepath, attr_name, key_type=None, as_set=False):
        """通用JSON加载"""
        try:
            if os.path.exists(filepath):
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if as_set:
                        setattr(self, attr_name, set(data))
                    elif key_type:
                        setattr(self, attr_name, {key_type(k): v for k, v in data.items()})
                    else:
                        getattr(self, attr_name).update(data)
        except Exception as e:
            logger.error(f"加载 {filepath} 失败: {e}")
    
    def _save_json(self, filepath, data):
        """通用JSON保存"""
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                if isinstance(data, set):
                    json.dump(list(data), f, ensure_ascii=False, indent=2)
                else:
                    json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存 {filepath} 失败: {e}")
    
    def save_mapping(self):
        self._save_json(MAPPING_FILE, self.user_mapping)
    
    def save_whitelist(self):
        self._save_json(WHITELIST_FILE, self.whitelist)
    
    def save_blacklist(self):
        self._save_json(BLACKLIST_FILE, self.blacklist)
    
    def save_pending(self):
        self._save_json(PENDING_VERIFY_FILE, self.pending_verify)
    
    def save_stats(self):
        self._save_json(STATS_FILE, self.statistics)
    
    # === 业务方法 ===
    def add_to_whitelist(self, user_id: int):
        self.whitelist.add(user_id)
        self.blacklist.discard(user_id)  # 从黑名单移除
        self.pending_verify.pop(user_id, None)
        self.statistics["verified_users"] += 1
        self.save_whitelist()
        self.save_blacklist()
        self.save_pending()
        self.save_stats()
    
    def add_to_blacklist(self, user_id: int):
        self.blacklist.add(user_id)
        self.whitelist.discard(user_id)
        self.pending_verify.pop(user_id, None)
        self.save_blacklist()
        self.save_whitelist()
        self.save_pending()
    
    def remove_from_blacklist(self, user_id: int):
        self.blacklist.discard(user_id)
        self.save_blacklist()
    
    def is_allowed(self, user_id: int) -> bool:
        """检查用户是否允许访问"""
        return user_id == self.owner_id or user_id in self.whitelist
    
    def is_blocked(self, user_id: int) -> bool:
        """检查用户是否被拉黑"""
        return user_id in self.blacklist

# 全局数据管理器
dm = DataManager()

# ==================== 验证系统 ====================
class VerificationSystem:
    """用户验证系统"""
    
    @staticmethod
    def generate_challenge() -> tuple[int, int, int]:
        """生成验证题目: 返回 (a, b, answer)"""
        a = random.randint(10, 50)
        b = random.randint(1, 9)
        return a, b, a + b
    
    @staticmethod
    async def start_verification(update: Update, user_id: int):
        """发起验证"""
        a, b, answer = VerificationSystem.generate_challenge()
        dm.pending_verify[user_id] = {"answer": answer, "attempts": 0}
        dm.save_pending()
        
        await update.message.reply_html(
            "🛡️ <b>安全验证</b>\n\n"
            "检测到新用户，请回答问题验证身份：\n\n"
            f"👉 <b>{a} + {b} = ?</b>\n\n"
            f"共有 <b>{MAX_FAIL_LIMIT}</b> 次机会"
        )
    
    @staticmethod
    async def check_answer(update: Update, user_id: int, user_input: str) -> bool:
        """
        检查答案
        返回: True=验证完成(成功或失败), False=还在验证中
        """
        if user_id not in dm.pending_verify:
            return True
        
        verify_data = dm.pending_verify[user_id]
        correct = verify_data["answer"]
        
        try:
            if int(user_input.strip()) == correct:
                dm.add_to_whitelist(user_id)
                await update.message.reply_html(
                    "✅ <b>验证通过！</b>\n\n"
                    "已获得使用权限，请重新发送 /start"
                )
                logger.info(f"用户 {user_id} 验证通过")
                return True
        except ValueError:
            pass
        
        # 答错
        verify_data["attempts"] += 1
        remaining = MAX_FAIL_LIMIT - verify_data["attempts"]
        
        if remaining <= 0:
            dm.add_to_blacklist(user_id)
            await update.message.reply_html(
                "❌ <b>验证失败</b>\n\n"
                "机会已用完，您已被永久拉黑。"
            )
            dm.statistics["blocked_attempts"] += 1
            dm.save_stats()
            logger.info(f"用户 {user_id} 验证失败，已拉黑")
            return True
        
        dm.save_pending()
        await update.message.reply_html(
            f"⚠️ <b>回答错误</b>\n\n"
            f"还剩 <b>{remaining}</b> 次机会"
        )
        return False

# ==================== 权限装饰器 ====================
def require_auth(func):
    """统一权限验证装饰器"""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        # 黑名单检查
        if dm.is_blocked(user_id):
            return
        
        # 白名单/主人检查
        if dm.is_allowed(user_id):
            return await func(update, context)
        
        # 验证中检查
        if user_id in dm.pending_verify:
            text = update.message.text if update.message else None
            if text:
                await VerificationSystem.check_answer(update, user_id, text)
            return
        
        # 发起新验证
        await VerificationSystem.start_verification(update, user_id)
        return
    
    return wrapper

def owner_only(func):
    """仅主人可用"""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != dm.owner_id:
            return
        return await func(update, context)
    return wrapper

# ==================== 命令处理器 ====================
@require_auth
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /start 命令"""
    user = update.effective_user
    
    if user.id == dm.owner_id:
        await update.message.reply_html(
            "👑 <b>欢迎回来，主人！</b>\n\n"
            "机器人已就绪。\n\n"
            "<b>可用命令：</b>\n"
            "/help - 帮助信息\n"
            "/stats - 查看统计\n"
            "/banlist - 黑名单管理\n"
            "/broadcast - 群发消息\n"
            "/clear - 清理缓存"
        )
    else:
        await update.message.reply_html(
            f"👋 你好，{user.mention_html()}！\n\n"
            "发送任何消息，我会帮你转达。"
        )

@require_auth
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /help 命令"""
    user = update.effective_user
    
    if user.id == dm.owner_id:
        text = (
            "📖 <b>使用指南</b>\n\n"
            "<b>回复用户：</b>直接回复转发的消息\n\n"
            "<b>管理命令：</b>\n"
            "• /stats - 运行统计\n"
            "• /banlist - 黑名单列表\n"
            "• /unban [用户ID] - 解除拉黑\n"
            "• /broadcast [消息] - 群发给所有白名单用户\n"
            "• /clear - 清理消息映射缓存\n\n"
            "<b>快捷操作：</b>\n"
            "转发消息后会显示控制面板，可一键拉黑"
        )
    else:
        text = "📖 直接发送消息即可，无需命令。"
    
    await update.message.reply_html(text)

@owner_only
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看统计信息"""
    stats = dm.statistics
    start_time = stats.get("start_time", "未知")
    
    await update.message.reply_html(
        "📊 <b>运行统计</b>\n\n"
        f"📅 启动时间: <code>{start_time[:10] if start_time else '未知'}</code>\n"
        f"📨 转发消息: <b>{stats.get('total_messages', 0)}</b>\n"
        f"💬 回复消息: <b>{stats.get('total_replies', 0)}</b>\n"
        f"✅ 已验证用户: <b>{stats.get('verified_users', 0)}</b>\n"
        f"🚫 拦截次数: <b>{stats.get('blocked_attempts', 0)}</b>\n\n"
        f"📝 当前映射: <b>{len(dm.user_mapping)}</b> 条\n"
        f"👥 白名单: <b>{len(dm.whitelist)}</b> 人\n"
        f"🚷 黑名单: <b>{len(dm.blacklist)}</b> 人"
    )

@owner_only
async def banlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看黑名单"""
    if not dm.blacklist:
        await update.message.reply_html("🚷 黑名单为空")
        return
    
    lines = [f"• <code>{uid}</code>" for uid in list(dm.blacklist)[:20]]
    text = "🚷 <b>黑名单</b>\n\n" + "\n".join(lines)
    
    if len(dm.blacklist) > 20:
        text += f"\n\n... 共 {len(dm.blacklist)} 人"
    
    text += "\n\n使用 /unban [用户ID] 解封"
    await update.message.reply_html(text)

@owner_only
async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """解除拉黑"""
    if not context.args:
        await update.message.reply_html("用法: /unban [用户ID]")
        return
    
    try:
        user_id = int(context.args[0])
        if user_id in dm.blacklist:
            dm.remove_from_blacklist(user_id)
            await update.message.reply_html(f"✅ 已解封用户 <code>{user_id}</code>")
        else:
            await update.message.reply_html("该用户不在黑名单中")
    except ValueError:
        await update.message.reply_html("请输入有效的用户ID")

@owner_only
async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """群发消息给所有白名单用户"""
    if not context.args:
        await update.message.reply_html(
            "📢 <b>群发功能</b>\n\n"
            "用法: /broadcast [消息内容]\n"
            f"将发送给 {len(dm.whitelist)} 位白名单用户"
        )
        return
    
    message = ' '.join(context.args)
    success, failed = 0, 0
    
    status_msg = await update.message.reply_html("📤 正在发送...")
    
    for user_id in dm.whitelist:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"📢 <b>系统通知</b>\n\n{message}",
                parse_mode=ParseMode.HTML
            )
            success += 1
        except:
            failed += 1
    
    await status_msg.edit_text(
        f"📢 <b>群发完成</b>\n\n"
        f"✅ 成功: {success}\n"
        f"❌ 失败: {failed}",
        parse_mode=ParseMode.HTML
    )

@owner_only
async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """清除映射缓存"""
    count = len(dm.user_mapping)
    dm.user_mapping.clear()
    dm.save_mapping()
    await update.message.reply_html(f"🗑️ 已清除 {count} 条消息映射")

# ==================== 消息处理器 ====================
@require_auth
async def forward_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """转发用户消息给主人"""
    user = update.effective_user
    message = update.message
    
    if user.id == dm.owner_id:
        if not message.reply_to_message:
            await message.reply_html("💡 请回复转发的消息来回复用户")
        return
    
    # 构建信息头
    username = f"@{user.username}" if user.username else "无"
    info_text = (
        f"📩 <b>新消息</b>\n\n"
        f"👤 {user.mention_html()}\n"
        f"🆔 <code>{user.id}</code>\n"
        f"🔗 {username}\n\n"
        f"👇 回复下方消息以回复该用户"
    )
    
    try:
        await context.bot.send_message(
            chat_id=dm.owner_id, 
            text=info_text, 
            parse_mode=ParseMode.HTML
        )
        
        forwarded = await message.forward(chat_id=dm.owner_id)
        dm.user_mapping[forwarded.message_id] = user.id
        dm.save_mapping()
        
        # 发送控制面板
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🚫 拉黑", callback_data=f"ban:{user.id}"),
                InlineKeyboardButton("📋 用户信息", callback_data=f"info:{user.id}")
            ]
        ])
        await context.bot.send_message(
            chat_id=dm.owner_id,
            text=f"⚙️ 操作面板 | 用户: <code>{user.id}</code>",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )
        
        dm.statistics["total_messages"] += 1
        dm.save_stats()
        
        await message.reply_html("✅ 已送达")
        logger.info(f"转发消息: {user.id} -> 主人")
        
    except TelegramError as e:
        logger.error(f"转发失败: {e}")
        await message.reply_html("❌ 发送失败，请稍后重试")

@owner_only
async def reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """主人回复消息"""
    message = update.message
    replied_id = message.reply_to_message.message_id
    target_user = dm.user_mapping.get(replied_id)
    
    if not target_user:
        await message.reply_html("⚠️ 找不到原始用户记录")
        return
    
    try:
        await message.copy(chat_id=target_user)
        dm.statistics["total_replies"] += 1
        dm.save_stats()
        await message.reply_html("✅ 已发送")
        logger.info(f"回复消息: 主人 -> {target_user}")
    except TelegramError as e:
        error_msg = f"❌ 发送失败: <code>{e}</code>"
        if "blocked" in str(e).lower():
            error_msg += "\n\n该用户可能已拉黑机器人"
        await message.reply_html(error_msg)

# ==================== 回调处理器 ====================
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理按钮回调"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != dm.owner_id:
        return
    
    action, user_id_str = query.data.split(":")
    user_id = int(user_id_str)
    
    if action == "ban":
        if user_id in dm.blacklist:
            dm.remove_from_blacklist(user_id)
            status = "✅ 已解封"
            btn_text = "🚫 拉黑"
        else:
            dm.add_to_blacklist(user_id)
            status = "🚫 已拉黑"
            btn_text = "✅ 解封"
        
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(btn_text, callback_data=f"ban:{user_id}"),
                InlineKeyboardButton("📋 用户信息", callback_data=f"info:{user_id}")
            ]
        ])
        
        await query.edit_message_text(
            f"⚙️ 操作面板 | 用户: <code>{user_id}</code>\n状态: {status}",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )
        
    elif action == "info":
        in_whitelist = "✅ 是" if user_id in dm.whitelist else "❌ 否"
        in_blacklist = "✅ 是" if user_id in dm.blacklist else "❌ 否"
        msg_count = sum(1 for uid in dm.user_mapping.values() if uid == user_id)
        
        await query.answer(
            f"白名单: {in_whitelist}\n黑名单: {in_blacklist}\n消息数: {msg_count}",
            show_alert=True
        )

# ==================== 启动和错误处理 ====================
async def post_init(application: Application):
    """启动后初始化"""
    dm.load_all()
    
    try:
        await application.bot.send_message(
            chat_id=dm.owner_id,
            text=(
                f"🚀 <b>机器人已启动 (V{BOT_VERSION})</b>\n\n"
                f"📊 已加载 {len(dm.user_mapping)} 条映射\n"
                f"👥 白名单 {len(dm.whitelist)} 人\n"
                f"🚷 黑名单 {len(dm.blacklist)} 人\n\n"
                "输入 /help 查看命令"
            ),
            parse_mode=ParseMode.HTML
        )
    except TelegramError as e:
        logger.error(f"启动通知发送失败: {e}")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """错误处理"""
    logger.error("异常:", exc_info=context.error)

# ==================== 主函数 ====================
def main():
    """启动机器人"""
    dm._load_config()  # 预加载配置获取token
    
    application = (
        Application.builder()
        .token(dm.bot_token)
        .post_init(post_init)
        .build()
    )
    
    # 命令处理器
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("banlist", banlist_command))
    application.add_handler(CommandHandler("unban", unban_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("clear", clear_command))
    
    # 回调处理器
    application.add_handler(CallbackQueryHandler(callback_handler))
    
    # 消息处理器
    application.add_handler(MessageHandler(
        filters.Chat(dm.owner_id) & filters.REPLY & ~filters.COMMAND,
        reply_handler
    ))
    application.add_handler(MessageHandler(
        filters.ALL & ~filters.COMMAND,
        forward_message_handler
    ))
    
    application.add_error_handler(error_handler)
    
    logger.info(f"机器人启动中 (V{BOT_VERSION})...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
