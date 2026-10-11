# -*- coding: utf-8 -*-
"""issue #220：把配置恢复出厂默认。

**为什么单独一个模块**：`modern_settings_dialog.py` 受架构红线约束
（`tests/test_architecture.py::MODERN_SETTINGS_DIALOG_PY_LINE_BUDGET`），
余量只够挂一个按钮和一次 connect。重置的全部逻辑放在这里，对话框只做转发。

**为什么机制是「写完整默认值」**：不能让调用方写 ``null`` 或删键表达"撤掉覆写"。
`Config.reload()` 对 reload 白名单内每个键的处理是

    if key not in raw:
        continue                                    # 删键 → 保留内存现值
    if value is None and key not in _NULL_ACCEPTING_KEYS:
        continue                                    # 写 null → 同样保留内存现值
    self.data[key] = value

而 ``_NULL_ACCEPTING_KEYS`` 只有 ``context_menu_layout`` 一个键。实测（对 124 个
白名单键）：写 null 生效 1/124，删键生效 0/124，**写完整默认值生效 124/124**。
所以这里直接把 ``config.data`` 换成一份完整的出厂数据。那个唯一吃 null 的键本身
也在保留清单里（见 ``PRESERVE_KEYS``），于是"本模块一个 null 都不写"是恒真的，
不靠"恰好只有它能吃 null"这种巧合。

**什么保留、什么重置**：区分三类——「偏好」是"我希望它怎么表现"（重置）；「资产」
是"我创作的东西"、「本机环境」是"我这台机器上东西在哪"（保留，抹了不可重建）；
「运行时几何」是"它此刻待在哪"（保留，见下）。

**为什么位置不算「设置」**：``rx`` / ``ry`` / ``screen_name`` 的出厂默认是 ``None``
（= "还没摆过位置"），而 ``reload()`` 只对 ``_NULL_ACCEPTING_KEYS`` 采纳盘上的显式
null——重置把它们写成 null 落盘后，**正在运行的桌宠进程会忽略它、保留旧位置**，
随后用户拖一下桌宠（``save_position``）就把旧位置写回磁盘，重置被静默回滚，正是
缺陷 15 的同一个坑。实测：盘上 ``rx=null``，主进程 ``reload()`` 后仍是 ``0.1``，
``save()`` 后盘上又变回 ``0.1``。

要把它们真正重置，只能往 ``_NULL_ACCEPTING_KEYS`` 加键，而
``tests/test_config_reload_null.py`` 把那个集合钉成了恰好一个键。不值得为"位置"
付这个代价：位置本身已经自愈——``window_placement.restore_position`` 与
``overlay_shell._restore_position`` 都把坐标钳回可用区、副屏掉线时回退当前屏，
"桌宠跑到屏幕外"并不靠重置兜底。而 issue #220 的原话是恢复默认**设置**；``scale``
与 ``character`` 在外观页里确实是可以改的设置，它们照重置。

**落盘复用既有路径**：``Config.save()`` → ``_redacted_data()``（剔除明文 API Key）
→ PID 后缀临时文件 → ``atomic_replace_with_retry()``。配置文件在运行期是高频写入的
（桌宠挪一下 ``rx``/``ry`` 就落一次盘），原子写与重试是本模块的刚需而非保险。
"""
from __future__ import annotations

import copy
import tempfile

from .config import Config

#: 重置时保留的顶层键。
#:
#: - ``version``：``Config.reload()`` 迁移路径的入口，回退会走降级分支。
#: - ``dialogue_phrases`` / ``character_aliases``：用户一个字一个字写的台词、
#:   给角色起的名字，是创作资产。
#: - ``quick_launch_apps``：用户攒的快捷启动清单。
#: - ``context_menu_layout``：用户手改的右键菜单排布/别名/图标，同属创作资产。
#:   它同时是**唯一**出厂默认为 ``None`` 的偏好侧键（``_NULL_ACCEPTING_KEYS``），
#:   所以留着它还顺带把本模块的不变量收紧成"重置只写非 null 的完整默认值"——
#:   不再有任何键依赖 null 那条通道。不重置它不损失兜底能力：它自己的编辑器里
#:   本来就有「恢复默认布局」，而布局非法时 ``_save`` 会直接拒绝落盘
#:   （``menu_validation.source == "fallback"``），"布局被改坏"这个场景不存在。
#: - ``music_player_paths`` / ``pnpm_bin``：本机环境路径，重配成本高于配置本身
#:   的价值，且与"配置被改坏"无关。
#:
#: 注意 ``chat.providers[].api_key`` 不在此列——它是**嵌套**键，由 ``Config``
#: 自己的 secret 策略处理（``_redacted_data()`` 本就不写盘，key 只活在内存）。
PRESERVE_KEYS = frozenset(
    {
        "version",
        "dialogue_phrases",
        "character_aliases",
        "quick_launch_apps",
        "context_menu_layout",
        "music_player_paths",
        "pnpm_bin",
    }
)


#: 运行时几何 / 朝向，同样不进重置范围——但理由与 ``PRESERVE_KEYS`` 不同：
#: 它们不是"需要保护的价值"，而是**压根不属于「设置」**（模块 docstring 有完整
#: 推导：这些键出厂默认为 None，写盘后被运行中的进程忽略并回滚）。
#:
#: ``facing`` 与 ``rx``/``ry``/``screen_name`` 同属"每窗运行时状态"
#: （``slot_manager._SEED_EXCLUDE_KEYS`` 把这四个键一并排除在配置继承之外，
#: 理由同款："位置/朝向不继承"），所以这里也一并排除。
RUNTIME_GEOMETRY_KEYS = frozenset({"rx", "ry", "screen_name", "facing"})

#: 重置时一个键都不碰的顶层键全集。
_UNTOUCHED_KEYS = PRESERVE_KEYS | RUNTIME_GEOMETRY_KEYS


#: chat provider 下只存在于内存的明文字段（`_redacted_data()` 写盘时剔除）。
_CHAT_SECRET_FIELDS = ("api_key", "vision_api_key")


def _carry_chat_secrets(fresh: dict, old: dict) -> None:
    """把内存里的 chat provider 明文 key 搬到重置后的数据上（原地改 ``fresh``）。

    只对**两边都有的 provider** 生效：重置会删掉用户自建的 provider，给它单独留
    一份 key 没有意义，而多出来的"幽灵 provider"还会破坏 ``_merge_chat_data`` 的
    ``active_provider`` 回退（它按 providers 的键集合挑活跃项）。
    """
    old_chat = old.get("chat")
    new_chat = fresh.get("chat")
    if not isinstance(old_chat, dict) or not isinstance(new_chat, dict):
        return
    old_providers = old_chat.get("providers")
    new_providers = new_chat.get("providers")
    if not isinstance(old_providers, dict) or not isinstance(new_providers, dict):
        return
    for provider_id, new_provider in new_providers.items():
        if not isinstance(new_provider, dict):
            continue
        old_provider = old_providers.get(provider_id)
        if not isinstance(old_provider, dict):
            continue
        for field in _CHAT_SECRET_FIELDS:
            secret = old_provider.get(field)
            if isinstance(secret, str) and secret:
                new_provider[field] = secret


def default_config_data() -> dict:
    """返回一份完整的出厂默认配置。

    ``Config`` 的默认值内联在 ``Config.__init__`` 里（``config.py:717`` 起的一大段
    字面量 + 散落的模块常量），没有独立的工厂函数，所以这里用一个空临时目录造一份
    全新 ``Config`` 取它的 ``data``。三条安全性已核：

    - ``_migrate_legacy_config`` 在源码运行下第一行就 return
      （``APP_DIR_NAME == "dsh-pet-standalone"``）；
    - ``_seed_slot_config_from_main`` 只在设了 ``instance_id`` 时执行，这里不设；
    - 空目录下 ``self.path`` 不存在，``__init__`` 末尾的 ``reload()`` 直接 return。

    实测：全新 ``Config.data`` 的键集合与用户真实 ``config.json`` 的键集合差集为空
    （129 对 129），所以"重装一次"与"点恢复默认"得到的初始状态逐键相同。
    """
    with tempfile.TemporaryDirectory(prefix="dsh-pet-factory-") as tmp:
        return copy.deepcopy(Config(base=tmp).data)


def reset_to_defaults(config: Config) -> bool:
    """把 ``config`` 恢复出厂默认并落盘；返回是否写盘成功。

    调用方拿到的 ``config`` 是同一个对象，``data`` 被整体替换，随后走的
    ``save()`` / ``reload()`` 与用户手改设置走的是同一条路。
    """
    fresh = default_config_data()
    for key in _UNTOUCHED_KEYS:
        if key in config.data:
            fresh[key] = copy.deepcopy(config.data[key])
    _carry_chat_secrets(fresh, config.data)
    config.data = fresh
    return config.save()


# ------------------------------------------------------------------ 设置页接线
#: 按钮所在行的 setting_id。**它不是配置键**：这是一条命令，不是持久设置
#: （`docs/SETTINGS-CHANGE-GATES.md` 准入条款 1 把"一次性命令"明确排除在设置页
#: 准入之外），所以不进 `Config` 的默认值字典、不进 reload 白名单、也不参与域
#: claim——`modern_settings_dialog._rebuild_domain_navigation` 在行快照之后才创建
#: 它，本就不需要 claim（与 `settings_music.build_music_player_rows` 同口径）。
ROW_KEY = "restore_defaults"


def settings_rows(dialog) -> list:
    """构造「恢复默认设置」那一行（标签 + 按钮）。

    控件与行都在本模块构建，`modern_settings_dialog` 只留一行接线——照
    `settings_music.browser_media_rows` 的先例：那个文件的最后一次改动正是把设置行
    搬出去以压低 `MODERN_SETTINGS_DIALOG_PY_LINE_BUDGET`，本功能沿用同一手法。
    """
    from PySide6.QtWidgets import QPushButton  # 惰性导入：本模块的逻辑部分不需要 Qt

    from .settings_widgets import SettingRow

    button = QPushButton("恢复默认设置…")
    button.setObjectName("restoreDefaultsButton")
    button.clicked.connect(lambda: confirm_and_reset(dialog))
    return [
        SettingRow(
            ROW_KEY,
            "恢复默认设置",
            "把所有设置写回出厂默认，用于配置被改坏后自助恢复。"
            "自定义台词、角色名、快捷启动清单、右键菜单排布、"
            "音乐软件与 pnpm 路径会保留；"
            "桌宠当前的位置与朝向不会被挪动。",
            button,
        )
    ]


def confirm_and_reset(dialog) -> bool:
    """二次确认 → 恢复默认 → 关闭设置窗口。返回是否真的重置了。

    **为什么重置完就关窗**：`ModernSettingsDialog` 的保存语义是"随手关也保存"——
    `_save` / `reject` / `closeEvent` 三条路径都会拿**控件当前值**调
    `_write_config()` 写回配置（设计如此，见各方法 docstring）。重置之后控件里装的
    还是旧值，窗口若继续开着，用户下一次保存或关窗就把刚恢复的默认值覆盖回去——
    正是本功能要修的那种"点了没反应"。所以这里主动 `accept()` 收尾：它走
    `done(Accepted)`，不经过上述三条路径中的任何一条，也就不会回写。

    本函数对应的测试断言的是**磁盘上的最终状态**而不只是返回值，正是为了钉住
    "关窗没把旧值写回去"这一环。
    """
    from PySide6.QtWidgets import QMessageBox  # 惰性导入，同 settings_rows

    config = dialog.config
    answer = QMessageBox.question(
        dialog,
        "恢复默认设置",
        "确定把所有设置恢复为出厂默认吗？\n\n"
        "会保留：自定义台词、角色名、快捷启动清单、右键菜单排布、"
        "音乐软件与 pnpm 路径、API Key。\n"
        "会重置：其余全部设置，包括桌宠大小与角色。\n"
        "不会改动：桌宠当前的位置与朝向，以及多开子肥鱼各自的配置。",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,  # 默认落在"否"：这是破坏性操作，别让回车直接触发
    )
    if answer != QMessageBox.StandardButton.Yes:
        return False
    if not reset_to_defaults(config):
        QMessageBox.warning(dialog, "恢复默认设置", "写入配置文件失败，设置未改动。")
        return False
    # 置位既有的"已落盘、不必再写"闸门：accept() 本身不回写，但任何**后续**的
    # close()/reject() 若闸门没关，就会拿控件旧值调 _write_config() 把重置抹掉。
    # 这一步有测试钉住（去掉本行即红），不是防御性摆设。
    dialog._saved_via_button = True
    QMessageBox.information(dialog, "恢复默认设置", "已恢复出厂默认设置。")
    dialog.accept()
    return True
