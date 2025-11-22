# forwarder_bot_v6.py>加入拉黑功能
import random
import logging
import configparser
import json
import os
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode
from telegram.error import TelegramError

# --- 全局常量 ---
CONFIG_FILE = 'config.ini'
MAPPING_FILE = 'user_mapping.json'
# --- 新增安全配置 ---
ACCESS_GRANTED_LIST = [12345678, 87654321] # 请在此处填入白名单数字

# --- 日志配置 ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- 全局变量 ---
OWNER_ID = 0
BOT_TOKEN = ""
# 内存中的映射缓存
user_mapping = {}

# --- 核心功能函数 ---

def load_config():
    """加载配置文件并设置全局变量"""
    global OWNER_ID, BOT_TOKEN
    config = configparser.ConfigParser()
    if not os.path.exists(CONFIG_FILE):
        logger.critical(f"关键错误：配置文件 {CONFIG_FILE} 未找到！")
        exit()
    try:
        config.read(CONFIG_FILE)
        BOT_TOKEN = config['Telegram']['BOT_TOKEN']
        OWNER_ID = int(config['Telegram']['OWNER_ID'])
    except (KeyError, ValueError):
        logger.critical(f"关键错误：{CONFIG_FILE} 文件格式不正确或缺少必要字段。")
        exit()

def load_mapping():
    """从 JSON 文件加载消息ID映射到内存"""
    global user_mapping
    try:
        if os.path.exists(MAPPING_FILE):
            with open(MAPPING_FILE, 'r') as f:
                # JSON 键是字符串，加载时需要转换回整数
                user_mapping = {int(k): v for k, v in json.load(f).items()}
            logger.info(f"成功从 {MAPPING_FILE} 加载了 {len(user_mapping)} 条映射。")
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"加载映射文件失败: {e}。将创建一个新的空映射。")
        user_mapping = {}

def save_mapping():
    """将内存中的映射保存到 JSON 文件"""
    try:
        with open(MAPPING_FILE, 'w') as f:
            json.dump(user_mapping, f, indent=4)
    except IOError as e:
        logger.error(f"保存映射文件失败: {e}")

# --- Telegram 命令处理器 ---

async def post_init(application: Application) -> None:
    """机器人启动后的初始化操作"""
    load_mapping()
    try:
        await application.bot.send_message(
            chat_id=OWNER_ID,
            text="""
🚀 **机器人已启动 (V5)**

已具备持久化会话能力，重启后依然可以回复旧消息。

新增用户验证功能，答错3次永久拉黑。

可用指令: /clear 清理缓存
            """,
            parse_mode=ParseMode.MARKDOWN
        )
        logger.info("启动通知已成功发送给主人。")
    except TelegramError as e:
        logger.error(f"启动通知发送失败: {e}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /start 命令，为主人和普通用户显示不同消息"""
    user = update.effective_user
    if user.id == OWNER_ID:
        await update.message.reply_html(
            "👑 <b>你好，我的主人！</b>\n\n"
            "我已准备就绪，随时为您服务。\n\n"
            "您可以直接 <b>回复</b> 我转发的消息来与用户沟通。\n"
            "使用 /help 查看更多指令。"
        )
    else:
        await update.message.reply_html(
            f" {user.mention_html()}！\n\n"
            "可以发送任何消息。"
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /help 命令"""
    user = update.effective_user
    help_text = "<b>帮助信息</b>\n\n"
    if user.id == OWNER_ID:
        help_text += ("- <b>回复消息:</b> 直接使用Telegram的“回复”功能，即可将您的消息发送给原始用户。\n"
                      "- <code>/clear</code>: 清除所有消息的回复记录。当您觉得缓存过多时可以使用。")
    else:
        help_text += "无需任何命令。"
    await update.message.reply_html(help_text)

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """(仅限主人) 清除映射缓存和文件"""
    if update.effective_user.id != OWNER_ID:
        return # 如果不是主人，则静默忽略

    global user_mapping
    user_mapping.clear()
    if os.path.exists(MAPPING_FILE):
        os.remove(MAPPING_FILE)
    
    logger.info("映射缓存已被主人清除。")
    await update.message.reply_html("🗑️ 所有消息的回复映射已被成功清除。")

# --- Telegram 消息处理器 ---

async def forward_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理并转发所有非命令的陌生人消息"""
    user = update.effective_user
    message = update.message

    if user.id == OWNER_ID:
        if not message.reply_to_message:
            await message.reply_html("💡 <b>提示：</b>如需回复用户，请直接“回复”我转发的消息。")
        return

    # --- 优化的转发信息头 ---
    user_info = (
        f"📩 <b>新消息抵达</b>\n\n"
        f"👤 <b>来自:</b> {user.mention_html()}\n"
        f"🆔 <b>用户ID:</b> <code>{user.id}</code>\n"
        f"🔗 <b>用户名:</b> @{user.username if user.username else '无'}\n\n"
        f"👇 <b>请直接回复下方这条消息来回复该用户</b> 👇"
    )
    
    try:
        await context.bot.send_message(chat_id=OWNER_ID, text=user_info, parse_mode=ParseMode.HTML)
        forwarded_message = await message.forward(chat_id=OWNER_ID)
        
        user_mapping[forwarded_message.message_id] = user.id
        save_mapping() # 持久化存储
        logger.info(f"消息从 {user.id} 转发。映射已更新并保存。")

        await message.reply_html("✅送达。")
    except TelegramError as e:
        logger.error(f"转发消息失败: {e}")
        await message.reply_html("❌失败。")

async def reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理主人的回复，并将其发送给原始用户"""
    message = update.message
    replied_to_id = message.reply_to_message.message_id
    original_user_id = user_mapping.get(replied_to_id)
    
    if not original_user_id:
        await message.reply_html("⚠️ <b>无法回复</b>\n\n未找到此消息的原始发信人记录。")
        logger.warning(f"未找到消息ID {replied_to_id} 的映射，无法回复。")
        return

    try:
        await message.copy(chat_id=original_user_id)
        await message.reply_html("✅成功！")
        logger.info(f"已将主人的回复发送给用户 {original_user_id}")
    except TelegramError as e:
        error_message = f"❌ <b>发送回复失败</b>\n\n错误信息: <code>{e}</code>"
        # 针对用户拉黑机器人的情况给出更明确的提示
        if "bot was blocked by the user" in str(e):
            error_message += "\n\n<b>可能原因:</b> 该用户已经将机器人拉黑。"
        
        await message.reply_html(error_message)
        logger.error(f"发送回复给 {original_user_id} 失败: {e}")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """记录所有因更新引起的错误"""
    logger.error("处理更新时发生异常", exc_info=context.error)

def main() -> None:
    """主函数，配置并启动机器人"""
    load_config()

    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
    # 注册命令处理器
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("clear", clear_command))

    # 注册消息处理器
    application.add_handler(MessageHandler(filters.Chat(OWNER_ID) & filters.REPLY & ~filters.COMMAND, reply_handler))
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, forward_message_handler))
    
    application.add_error_handler(error_handler)

    logger.info("机器人启动中 (V4)...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

# --- 纯净验证补丁 (Pure QA Patch) ---
black_ledger = set()    # 黑名单
exam_paper = {}         # 考卷
attempt_tracker = {}    # 错误计数
MAX_FAIL_LIMIT = 3      # 最大尝试次数

def auth_challenge_layer(target_func_ptr):
    async def quiz_gen(incoming_pkg, *args_v, **kwargs_k):
        sender_identity = incoming_pkg.effective_user.id
        
        # 1. 检查黑名单 (直接无视)
        if sender_identity in black_ledger:
            return

        # 2. 检查白名单 (直接放行)
        if sender_identity == OWNER_ID or sender_identity in ACCESS_GRANTED_LIST:
            return await target_func_ptr(incoming_pkg, *args_v, **kwargs_k)

        # 3. 检查是否在“考试中”
        input_txt = getattr(incoming_pkg.message, 'text', None)
        
        if sender_identity in exam_paper:
            correct_ans = exam_paper[sender_identity]
            
            # 判卷时刻
            if input_txt and input_txt.strip() == str(correct_ans):
                # 答对了：加入白名单，清空记录
                ACCESS_GRANTED_LIST.append(sender_identity)
                del exam_paper[sender_identity]
                if sender_identity in attempt_tracker:
                    del attempt_tracker[sender_identity]
                await incoming_pkg.message.reply_html("✅ <b>验证通过！</b>\n\n已获得使用权限，请重新发送 /start。")
            else:
                # 答错逻辑
                current_mistakes = attempt_tracker.get(sender_identity, 0) + 1
                attempt_tracker[sender_identity] = current_mistakes
                remains = MAX_FAIL_LIMIT - current_mistakes
                
                if remains <= 0:
                    # 机会耗尽：拉黑
                    black_ledger.add(sender_identity)
                    del exam_paper[sender_identity]
                    del attempt_tracker[sender_identity]
                    try:
                        await incoming_pkg.message.reply_html(
                            "❌ <b>验证失败</b>\n\n"
                            "机会已耗尽，系统判定为恶意访问。\n"
                            "已被<b>永久拉黑</b>。"
                        )
                    except:
                        pass
                else:
                    # 还有机会：提示
                    try:
                        await incoming_pkg.message.reply_html(
                            f"⚠️ <b>回答错误</b>\n\n"
                            f"请核对算式后重试。\n"
                            f"还有 <b>{remains}</b> 次尝试机会。"
                        )
                    except:
                        pass
            return

        # 4. 发起提问 (初始化)
        num_a = random.randint(10, 50)
        num_b = random.randint(1, 9)
        exam_paper[sender_identity] = num_a + num_b
        attempt_tracker[sender_identity] = 0
        
        challenge_msg = (
            "🛡️ <b>安全验证系统</b>\n\n"
            "检测到陌生用户请求。请回答以下问题以验证身份：\n\n"
            f"👉 <b>{num_a} + {num_b} = ?</b>\n\n"
            f"您有 <b>{MAX_FAIL_LIMIT}</b> 次回答机会。"
        )
        
        try:
            await incoming_pkg.message.reply_html(challenge_msg)
        except:
            pass
        return

    return quiz_gen

# 注入逻辑
start_command = auth_challenge_layer(start_command)
help_command = auth_challenge_layer(help_command)
clear_command = auth_challenge_layer(clear_command)
forward_message_handler = auth_challenge_layer(forward_message_handler)
reply_handler = auth_challenge_layer(reply_handler)
# ------------------------

# --- 增强补丁：黑名单管理 (Ultimate Ban System) ---
from telegram import InlineKeyboardMarkup as UI_Markup, InlineKeyboardButton as UI_Button
from telegram.ext import CallbackQueryHandler as UI_Handler

# --- 独立配置区 ---
BAN_DB_FILE = 'manual_ban_list.json'
forbidden_realm = set()  # 独立的黑名单内存集合

# --- 持久化层 (Persistence Layer) ---
def sync_ban_storage(mode_code, uid_target=None):
    """
    黑名单数据同步控制器
    mode_code: 'L'=Load(加载), 'A'=Add(添加), 'R'=Remove(移除)
    """
    try:
        if mode_code == 'L':
            if os.path.exists(BAN_DB_FILE):
                with open(BAN_DB_FILE, 'r', encoding='utf-8') as fp:
                    content = json.load(fp)
                    forbidden_realm.update(content)
        elif mode_code in ['A', 'R']:
            if mode_code == 'A' and uid_target:
                forbidden_realm.add(uid_target)
            elif mode_code == 'R' and uid_target in forbidden_realm:
                forbidden_realm.remove(uid_target)
            # 立即回写文件，确保重启不丢失
            with open(BAN_DB_FILE, 'w', encoding='utf-8') as fp:
                json.dump(list(forbidden_realm), fp)
    except Exception:
        pass

# 初始化加载黑名单
sync_ban_storage('L')

# --- 逻辑拦截层 (Interception Layer) ---
def firewall_wrapper(core_func, is_panel_enabled=False):
    """
    全能防火墙装饰器
    is_panel_enabled: 是否为该函数开启管理员控制面板
    """
    async def security_proxy(evt_obj, ctx_agent, *args, **kwargs):
        # 1. 提取访问者ID
        visitor_id = evt_obj.effective_user.id
        
        # 2. 查验黑名单
        if visitor_id in forbidden_realm:
            # 被拉黑直接静默
            return 

        # 3. 放行核心逻辑
        await core_func(evt_obj, ctx_agent, *args, **kwargs)

        # 4. 后置注入：如果启用了面板且发送者不是主人
        if is_panel_enabled and visitor_id != OWNER_ID:
            try:
                ctrl_payload = f"CMD_BAN_TOGGLE:{visitor_id}"
                btn_label = "🛑 立即拉黑 (Ban)"
                
                ctrl_panel = UI_Markup([[UI_Button(btn_label, callback_data=ctrl_payload)]])
                
                await ctx_agent.bot.send_message(
                    chat_id=OWNER_ID,
                    text=f"👮‍♂️ <b>管理员控制台</b>\n操作对象: <code>{visitor_id}</code>",
                    reply_markup=ctrl_panel,
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass

    return security_proxy

# --- 交互响应层 (Interaction Layer) ---
async def admin_panel_callback(evt_obj, ctx_agent):
    """处理管理员点击按钮的事件"""
    query = evt_obj.callback_query
    await query.answer()
    
    raw_data = query.data
    if "CMD_BAN_TOGGLE:" in raw_data:
        target_uid_str = raw_data.split(":")[1]
        target_uid = int(target_uid_str)
        
        if target_uid in forbidden_realm:
            sync_ban_storage('R', target_uid)
            status_text = "✅ 已解封 (Active)"
            next_btn_text = "🛑 立即拉黑 (Ban)"
        else:
            sync_ban_storage('A', target_uid)
            status_text = "🚫 已封禁 (Banned)"
            next_btn_text = "🤝 解除封禁 (Unban)"
        
        new_markup = UI_Markup([[UI_Button(next_btn_text, callback_data=raw_data)]])
        try:
            await query.edit_message_text(
                text=f"👮‍♂️ <b>管理员控制台</b>\n操作对象: <code>{target_uid}</code>\n当前状态: {status_text}",
                reply_markup=new_markup,
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass

# --- 系统注入层 (System Injection Layer) ---
# 关键修复：保存原始函数的引用，防止无限递归
_orig_post_init = post_init

async def hooked_post_init(app_ref):
    """劫持启动流程"""
    # 1. 调用保存的原始启动函数（修复点）
    await _orig_post_init(app_ref)
    
    # 2. 动态挂载回调处理器
    app_ref.add_handler(UI_Handler(admin_panel_callback))
    
    try:
        await app_ref.bot.send_message(chat_id=OWNER_ID, text="🛡️ <b>拉黑功能已挂载</b>", parse_mode=ParseMode.HTML)
    except:
        pass

# --- 动态替换逻辑 (Monkey Patching) ---
# 1. 替换启动函数
post_init = hooked_post_init

# 2. 包裹所有关键入口函数
# 仅对转发消息开启 is_panel_enabled=True
start_command = firewall_wrapper(start_command)
help_command = firewall_wrapper(help_command)
clear_command = firewall_wrapper(clear_command)
reply_handler = firewall_wrapper(reply_handler)
forward_message_handler = firewall_wrapper(forward_message_handler, is_panel_enabled=True)

if __name__ == '__main__':
    main()
