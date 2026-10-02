"""内置中英翻译字典(无第三方依赖)。

两种语言:``zh-CN`` / ``en``。当前语言是进程级状态(``set_lang`` / ``get_lang``),
界面在**同一个窗口内就地刷新**即可立即生效,无需重启。

只用三件事:
- ``t(key, **kwargs)``:查字典并做 ``str.format`` 占位替换;
- ``set_lang(code)``:切换并返回归一化后的语言码(面板/托盘/窗口标题共用);
- ``system_language()``:缺省语言(旧配置没有 ``language`` 时的取值来源)。

检测口径:Windows 读用户界面语言(``GetUserDefaultUILanguage``),非 Windows 读
``LANG`` / ``LC_ALL`` / ``LANGUAGE`` 环境变量;两条路都失败 → 中文(``DEFAULT``)。
"""
from __future__ import annotations

import os

ZH = "zh-CN"
EN = "en"
SUPPORTED = (ZH, EN)
DEFAULT = ZH                       # 检测失败回退中文

# key -> (zh-CN, en)。英文优先取短词:面板最小 380x620 且要吃 DPI 缩放。
STRINGS: dict[str, tuple[str, str]] = {
    # ---------------- 物种 / 活动 / 食物 ----------------
    "species.cockroach": ("蟑螂", "Cockroach"),
    "species.fruitfly": ("果蝇", "Fruit fly"),
    "activity.idle": ("待机", "Idle"),
    "activity.explore": ("探索", "Exploring"),
    "activity.groom": ("清洁身体", "Grooming"),
    "activity.seek_food": ("觅食", "Seeking food"),
    "activity.eat": ("进食", "Eating"),
    "activity.escape": ("逃跑", "Fleeing"),
    "activity.rest": ("休息", "Resting"),
    "activity.turn": ("转向", "Turning"),
    "activity.takeoff": ("起飞", "Takeoff"),
    "activity.fly_wander": ("飞行", "Flying"),
    "activity.land": ("降落", "Landing"),
    "activity.frozen": ("已暂停", "Paused"),
    "activity.hidden": ("隐藏中", "Hidden"),
    "activity.busy": ("活动中", "Busy"),
    "food.crumb": ("面包屑", "Bread crumb"),
    "food.green": ("嫩叶", "Tender leaf"),
    "food.grease": ("油脂球（蟑螂爱吃）", "Fat ball (roach fave)"),
    "food.vinegar": ("果醋滴（果蝇爱吃）", "Vinegar drop (fly fave)"),

    # ---------------- 控制面板 ----------------
    "panel.subtitle": ("桌面昆虫", "Desktop insects"),
    "panel.tagline": ("慢慢靠近它，或放一点食物。", "Move closer, or leave a little food."),
    "panel.add_roach": ("＋ 添加蟑螂", "+ Add cockroach"),
    "panel.add_fly": ("＋ 添加果蝇", "+ Add fruit fly"),
    "panel.col_name": ("宠物", "Pet"),
    "panel.col_activity": ("正在做什么", "Activity"),
    "panel.col_food": ("饱食", "Full"),
    "panel.feed": ("喂它", "Feed"),
    "panel.freeze": ("暂停", "Pause"),
    "panel.resume": ("继续", "Resume"),
    "panel.hide": ("隐藏", "Hide"),
    "panel.recall": ("召回", "Recall"),
    "panel.remove": ("移除", "Remove"),
    "panel.memory": ("记住的事", "Memories"),
    "panel.settings": ("设置", "Settings"),
    "panel.pause_all": ("全部暂停", "Pause all"),
    "panel.resume_all": ("全部继续", "Resume all"),
    "panel.collapse": ("收起", "Collapse"),
    "panel.empty_hint": ("点击上方按钮，添加第一只宠物。",
                         "Use the buttons above to add your first pet."),
    "panel.select_hint": ("选择一只宠物，即可投喂、暂停或隐藏。",
                          "Select a pet to feed, pause or hide it."),
    "panel.count": ("桌面 {on} / {total} 只    隐藏 {hidden} 只",
                    "On desk {on} / {total}    Hidden {hidden}"),
    "panel.full_hint": ("桌面已有 {total} 只；隐藏或移除后可继续添加。",
                        "The desk already holds {total} pets. Hide or remove one to add more."),
    "panel.mood_calm": ("很放松", "Relaxed"),
    "panel.mood_wary": ("有些警觉", "A bit wary"),
    "panel.hidden_detail": ("隐藏中，随时可召回", "Hidden, ready to recall"),
    "panel.added": ("已添加 {name}。按住虫体可拖动。",
                    "Added {name}. Drag the insect to move it."),
    "panel.fed": ("已在它附近放下食物，饿了就会过去吃。",
                  "Food dropped nearby; it will eat when hungry."),
    "panel.paused_all": ("全部已暂停。再次点击可继续。", "All pets paused. Click again to resume."),
    "panel.resumed_all": ("全部继续活动。", "All pets are moving again."),
    "panel.no_slot": ("桌面名额已满，先隐藏另一只再召回。",
                      "No free slot. Hide another pet first, then recall."),
    "panel.recalled": ("已召回 {count} 只。", "Recalled {count} pets."),
    "panel.recalled_rest": ("另有 {count} 只等待桌面名额。",
                            "{count} still waiting for a free slot."),
    "panel.removed": ("已移除宠物，历史档案已保留。",
                      "Pet removed; its archive is kept."),
    "panel.remove_title": ("移除宠物", "Remove pet"),
    "panel.remove_ask": ("从桌面移除这只宠物？历史档案会保留。",
                         "Remove this pet from the desk? Its archive is kept."),

    # ---------------- 记忆窗口 ----------------
    "memory.title": ("{name} · 记住的事", "{name} · Memories"),
    "memory.recent": ("最近的经历", "Recent experiences"),
    "memory.empty": ("还没有留下经历。喂它、慢慢靠近它，再回来看看。",
                     "No memories yet. Feed it, move closer, then come back."),
    "memory.refresh": ("刷新", "Refresh"),
    "memory.clear": ("清除记忆…", "Clear memories…"),
    "memory.clear_title": ("清除记忆", "Clear memories"),
    "memory.clear_ask": ("永久清除这只宠物的记忆？此操作无法撤销。",
                         "Permanently erase this pet's memories? This cannot be undone."),

    # ---------------- 设置窗口 ----------------
    "settings.title": ("NeuroPet · 设置", "NeuroPet · Settings"),
    "settings.language": ("语言", "Language"),
    "settings.size_selected": ("{name} 的显示大小", "Display size for {name}"),
    "settings.size_none": ("宠物显示大小（请先选中桌面宠物）",
                           "Pet display size (select a pet on the desk first)"),
    "settings.startup": ("启动时打开控制面板", "Open the panel at startup"),
    "settings.click_feed": ("在桌面点击放食物（自动结束）",
                            "Click the desktop to drop food (auto-stops)"),
    "settings.hint": ("关闭面板会收起程序，宠物继续活动。\n从系统托盘可再次打开。",
                      "Closing the panel hides the app; pets keep moving.\n"
                      "Reopen it from the system tray."),
    "settings.quit": ("保存并退出程序", "Save and quit"),
    # 语言选项本身两种语言下都不翻译(简体中文 / English 是语言自称),
    # 所以任何语言的用户都能找到自己的那一项。
    "settings.language_zh": ("简体中文", "简体中文"),
    "settings.language_en": ("English", "English"),

    # ---------------- 系统托盘 ----------------
    "tray.title": ("NeuroPet 桌宠(左键:显示/隐藏面板,右键:菜单)",
                   "NeuroPet pets (left click: show/hide panel, right click: menu)"),
    "tray.panel": ("显示控制面板", "Show panel"),
    "tray.feeding": ("投喂模式", "Feeding mode"),
    "tray.quit": ("退出", "Quit"),

    # ---------------- 运行期提示 ----------------
    "error.desk_full": ("桌面已有 {total} 只宠物。先隐藏或移除一只，再添加。",
                        "The desk already holds {total} pets. Hide or remove one first."),
}

_current: list[str] = [DEFAULT]


def normalize(code: str | None) -> str | None:
    """``zh``/``zh_CN``/``zh-CN``/``en_US`` → 支持的语言码;不认识 → ``None``。"""
    if not isinstance(code, str):
        return None
    tag = code.strip().replace("_", "-").lower()
    if not tag:
        return None
    if tag.startswith("zh"):
        return ZH
    if tag.startswith("en"):
        return EN
    return None


def system_language() -> str:
    """系统语言:中文系统 → 中文,其余 → 英文;检测失败 → 中文。"""
    try:
        if os.name == "nt":
            import ctypes
            lid = int(ctypes.windll.kernel32.GetUserDefaultUILanguage())
            # LANGID 主语种:0x04=中文、0x09=英文;覆盖自定义区域设置。
            primary = lid & 0x3FF
            if primary == 0x04:
                return ZH
            if primary == 0x09:
                return EN
        else:
            for name in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
                found = normalize(os.environ.get(name))
                if found:
                    return found
    except Exception:
        pass
    return DEFAULT


def resolve(code: str | None) -> str:
    """配置里的语言取值:支持的语言码直接用,缺失/不认识 → 系统语言。"""
    return normalize(code) or system_language()


def get_lang() -> str:
    return _current[0]


def set_lang(code: str | None) -> str:
    """切换当前语言(进程级),返回归一化后的语言码。"""
    lang = normalize(code) or system_language()
    _current[0] = lang
    return lang


def t(key: str, **kwargs) -> str:
    """查字典:缺 key 回退 key 本身(不抛异常,便于新加文案先跑起来)。"""
    entry = STRINGS.get(key)
    text = key if entry is None else (entry[0] if get_lang() == ZH else entry[1])
    return text.format(**kwargs) if kwargs else text
