# -*- coding: utf-8 -*-
"""issue #220：设置页需要一个「恢复默认设置」入口，把改坏的配置拉回出厂状态。

**现象**：设置页 7 个分页、80+ 个可持久化键，散布在 `_add_page` 的各处；全局搜
「恢复默认」只有菜单编排域的两处局部重置（`settings_menu_layout_editor.py` 的
「恢复默认名称与图标」/「恢复默认布局」）。对不熟悉配置文件的小白用户，改坏之后
没有任何「回到出厂」的自助路径。

**反馈源**：B 站 v3.1.0 发布视频评论区（BV1478X6iEAN）。那位用户把 chat 的 API
地址填成了本机地址——产品里 `pet/self_talk_voice.py` 的 `DEFAULT_SERVER` 就是
`http://127.0.0.1:9880`，抄错字段完全可复现。维护者当时手工引导他填回
`https://api.deepseek.com` / `deepseek-v4-flash`，**而这两条逐字就是
`config._default_chat_data()` 的出厂默认值**——也就是说，维护者做的正是本模块
要自动化掉的那件事。

**口径**：「恢复默认」= 把**偏好类**键写回出厂默认；用户创作的资产（自写台词、
角色别名）、本机环境路径（音乐软件、pnpm）、以及运行时几何（位置/朝向）保留。

**机制只能是「写完整默认值」**，不能写 `null` 也不能删键：`Config.reload()` 对
白名单内每个键的处理是

    if key not in raw:                                   continue   # 删键
    if value is None and key not in _NULL_ACCEPTING_KEYS: continue   # 写 null
    self.data[key] = value

而 `_NULL_ACCEPTING_KEYS` 只有 `context_menu_layout` 一个键。实测（124 个白名单
键）：写 null 生效 1/124，删键生效 0/124，写完整默认值生效 124/124。

本文件按行为切片逐个钉红→绿：偏好键 → 保留清单 → API Key → 大小与角色 →
运行时几何 → 跨进程可见性。
"""
from __future__ import annotations

import json

import pytest

import pet.chat.models as chat_models
from pet import settings_reset
from pet.config import Config


class FakeStore:
    """内存假 keyring，口径照 `tests/test_config_key_migration.py` 的同名替身。

    必须替掉真实 keyring：`_migrate_plaintext_keys_to_keyring` 在 reload 末尾会把
    内存里的明文 key 写进 keyring 并从 `self.data` 弹掉，若用真实实现，本文件会
    往用户本机凭据管理器（service `dsh-pet-standalone`，ref `provider/openai-main`，
    正是真实条目）写入测试值，且本地（有 keyring）与 CI
    （`PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring`）结论分叉。
    """

    shared: dict = {}

    def __init__(self, service_name="dsh-pet-standalone"):
        self.service_name = service_name

    @property
    def available(self):
        return True

    def get(self, ref):
        return self.shared.get(ref, "") if ref else ""

    def set(self, ref, value):
        if not ref:
            return False
        self.shared[ref] = value
        return True


@pytest.fixture(autouse=True)
def _fake_secret_store(monkeypatch):
    FakeStore.shared = {}
    monkeypatch.setattr(chat_models, "SecretStore", FakeStore)


class _ProviderView:
    """`Config.resolve_api_key()` 只要求一个带 `api_key_ref` / `api_key` 的对象。"""

    def __init__(self, config, provider_id):
        data = config.data["chat"]["providers"].get(provider_id, {})
        self.api_key_ref = data.get("api_key_ref") or f"provider/{provider_id}"
        self.api_key = data.get("api_key") or ""


def _configs(tmp_path):
    """返回 (出厂参照, 待改坏) 两份互不干扰的 Config。

    出厂参照用一个从未被写过的临时目录，所以它 `data` 里的就是出厂默认值——
    这正是「恢复默认」要回到的目标，也是本文件所有断言的对照组。
    """
    return (
        Config(base=str(tmp_path / "factory")),
        Config(base=str(tmp_path / "dirty")),
    )


def test_reset_restores_preference_keys_to_factory_default(tmp_path):
    """偏好类键（数值 / 布尔）改坏后，重置必须逐键回到出厂默认。"""
    factory, config = _configs(tmp_path)

    mutations = {
        "self_talk_min_interval": 45.0,  # 数值，且与 self_talk_max_interval 配对
        "on_top": False,  # 布尔
        "self_talk_enabled": True,  # 布尔
    }
    for key, broken in mutations.items():
        assert broken != factory.get(key), f"{key} 的改坏值碰巧等于出厂默认，这个键测不到东西"
        config.set(key, broken)
        assert config.get(key) == broken, f"{key} 没被改坏"
    config.save()

    assert settings_reset.reset_to_defaults(config) is True

    config.reload()
    for key in mutations:
        assert config.get(key) == factory.get(key), f"{key} 重置后没有回到出厂默认"


def test_reset_preserves_user_assets_and_machine_paths(tmp_path):
    """重置不得抹掉「用户创作的东西」与「本机环境里东西在哪」。

    口径：偏好类键重置，但这两类保留——后者的值跟「配置被改坏」没有因果关系，
    前者丢了不可重建（用户一个字一个字写的台词、给角色起的名字）。
    """
    _, config = _configs(tmp_path)

    preserved = {
        # 清洗后的规范形状（_clean_phrase_events 会补出 agents 层）
        "dialogue_phrases": {"global": {"click": ["你好呀"]}, "agents": {}},
        "quick_launch_apps": [
            {"name": "记事本", "path": "C:/Windows/notepad.exe", "kind": "application"}
        ],
        # 用户手改的菜单排布：出厂默认是 None，且它是唯一吃 null 的键
        # （_NULL_ACCEPTING_KEYS）。保留它 = 本模块一个 null 都不写。
        "context_menu_layout": {
            "nodes": [{"type": "action", "id": "settings", "alias": "我的设置"}]
        },
        "music_player_paths": {"netease": "C:/Music/cloudmusic.exe"},
        "pnpm_bin": "C:/nodejs/pnpm.cmd",
    }
    config.set_character_alias("shenshen", "小蓝")
    for key, value in preserved.items():
        config.set(key, value)
    config.save()

    # 先确认这些值真的落到了盘上。形状被清洗掉的话在这里就报出来，
    # 而不是伪装成「重置把它们抹了」——那是两回事。
    config.reload()
    assert config.get("character_aliases") == {"shenshen": "小蓝"}, "角色别名没落盘"
    for key, value in preserved.items():
        assert config.get(key) == value, f"{key} 的测试值没落盘，先修测试形状"

    assert settings_reset.reset_to_defaults(config) is True

    config.reload()
    assert config.character_alias("shenshen") == "小蓝", "重置抹掉了用户起的角色名"
    for key, value in preserved.items():
        assert config.get(key) == value, f"重置抹掉了 {key}"


def test_reset_restores_size_and_character(tmp_path):
    """大小与角色是「设置」（外观页里真能改），重置必须拉回出厂默认。"""
    factory, config = _configs(tmp_path)

    mutations = {"scale": 3.0, "character": "some-other-character"}
    for key, broken in mutations.items():
        assert broken != factory.get(key), f"{key} 的改坏值碰巧等于出厂默认"
        config.set(key, broken)
    config.save()

    assert settings_reset.reset_to_defaults(config) is True

    config.reload()
    for key in mutations:
        assert config.get(key) == factory.get(key), f"{key} 没回到出厂默认"


def test_reset_leaves_runtime_geometry_alone(tmp_path):
    """位置 / 所在屏 / 朝向不属于「设置」，重置一个都不许动。

    这三个键的出厂默认是 ``None``（= "还没摆过位置"），而 ``reload()`` 只对
    ``_NULL_ACCEPTING_KEYS`` 采纳盘上的显式 null。若把它们写回 None 落盘，正在
    运行的桌宠进程会忽略它、保留旧位置，随后拖动触发的 ``save_position`` 又把旧
    位置写回磁盘——重置被静默回滚（缺陷 15 的同一个坑）。实测：盘上 ``rx=null``，
    主进程 ``reload()`` 后仍是 ``0.1``，``save()`` 后盘上又变回 ``0.1``。

    要真重置它们得付"往 ``_NULL_ACCEPTING_KEYS`` 加键 + 改
    ``test_config_reload_null.py`` 那条守卫断言"的代价（该测试把集合钉成恰好一个
    键）。不值得：位置本身已由 ``restore_position`` 的钳制与副屏掉线回退自愈，
    且 issue #220 的原话是恢复默认**设置**。
    """
    factory, config = _configs(tmp_path)

    geometry = {
        "rx": 0.1,
        "ry": 0.9,
        "screen_name": "SOME-OTHER-SCREEN",
        "facing": "right",
    }
    for key, current in geometry.items():
        assert current != factory.get(key), f"{key} 的测试值碰巧等于出厂默认"
        config.set(key, current)
    config.save()

    assert settings_reset.reset_to_defaults(config) is True

    config.reload()
    for key, current in geometry.items():
        assert config.get(key) == current, f"重置动了运行时几何 {key}"


def test_reset_never_writes_a_key_reload_would_ignore(tmp_path):
    """通版守卫：重置落盘的每个键，``reload()`` 都必须真的采纳。

    ``reload()`` 对白名单键有两条 ``continue``——「键缺失」与「值恰为 null 且不在
    ``_NULL_ACCEPTING_KEYS``」。任一命中都 = 静默保留内存现值 = 重置被无视。逐键挑
    几个断言的测试挡不住"整体改成写 null 或删键"这类回退，这条按全量键集比对。

    为此先把出厂默认为 ``None`` 的几个键都设成实值，否则断言分不清"重置写了 null"
    与"这个键本来就还没有值"。
    """
    from pet.config import _NULL_ACCEPTING_KEYS

    factory, config = _configs(tmp_path)
    # 出厂默认恰为 None 的键总共四个（实测 124 个白名单键里只有这四个）：
    # rx / ry / screen_name 是运行时位置，context_menu_layout 是"没有覆写菜单"。
    for key in ("rx", "ry", "screen_name", "context_menu_layout"):
        assert factory.get(key) is None, f"前提变了：{key} 的出厂默认不再是 None"
        config.set(key, "PLACED")

    assert settings_reset.reset_to_defaults(config) is True

    raw = json.loads(config.path.read_text(encoding="utf-8"))
    missing = set(factory.data) - set(raw)
    assert not missing, f"重置后盘上丢了这些键（reload 会跳过它们）：{sorted(missing)}"
    ignored = {
        key: value
        for key, value in raw.items()
        if value is None and key not in _NULL_ACCEPTING_KEYS
    }
    assert not ignored, f"重置往盘上写了 null，reload 会静默忽略：{sorted(ignored)}"


def test_reset_keeps_chat_api_key(tmp_path):
    """重置不得清空用户已配置的 API Key（keyring 不可用时的内存兜底路径）。

    这是本功能能不能救回 B 站那位用户的关键：key 的出厂默认是**空串**，若连 key
    一起重置，他"恢复默认"之后照样连不上——而维护者当时给的第三条解决办法恰恰是
    "自己在 DS 官网创建一个 key"。

    keyring 不可用时 key 只活在内存（``_redacted_data()`` 写盘时剔除明文），重置
    整体替换 ``config.data`` 会连它一起换掉，所以必须显式搬过去。keyring 可用时
    走的是另一条路（key 在 keyring，``api_key_ref`` 保引用），见
    ``test_main_process_does_not_revert_reset``。
    """
    _, config = _configs(tmp_path)
    provider = config.data["chat"]["providers"]["openai-main"]
    provider["api_key"] = "sk-memory-only"
    provider["vision_api_key"] = "sk-vision-memory-only"
    config.save()

    # 前置确认：key 确实没落盘。落了盘说明 _redacted_data 失效，那是另一个缺陷，
    # 不该在这里伪装成"测试形状不对"。
    on_disk = json.loads(config.path.read_text(encoding="utf-8"))
    assert "api_key" not in on_disk["chat"]["providers"]["openai-main"], "明文 key 落盘了"

    assert settings_reset.reset_to_defaults(config) is True

    provider = config.data["chat"]["providers"]["openai-main"]
    assert provider["api_key"] == "sk-memory-only", "重置清空了 API Key"
    assert provider["vision_api_key"] == "sk-vision-memory-only", "重置清空了视觉 API Key"


def test_reset_keeps_config_version(tmp_path):
    """version 必须留在当前值：它是 Config 迁移路径的入口，回退会走降级分支。

    断言刻意**不经过 reload()**：``reload()`` 末尾有 ``self.data["version"] = 4``
    的硬写，reload 之后断言会退化成"恒等于 4"而测不到 PRESERVE 逻辑。这里用一个
    非默认值证明确实是"被保留了"而不是"碰巧等于出厂默认"。
    """
    _, config = _configs(tmp_path)

    config.data["version"] = 3
    assert settings_reset.reset_to_defaults(config) is True

    assert config.data["version"] == 3, "重置把 version 拉回了出厂值"


# ------------------------------------------------------- 设置页 seam（门禁 §2）
def _build_dialog(tmp_path, monkeypatch, *, confirm: bool):
    """造一个真实的 ModernSettingsDialog，并把 QMessageBox 换成可脚本化的替身。

    返回 (dialog, config, messages)。messages 记录弹过的框，供断言文案与默认按钮。
    """
    from PySide6.QtWidgets import QApplication, QMessageBox

    import pet.modern_settings_dialog as settings_mod
    from pet.modern_settings_dialog import ModernSettingsDialog
    from pet.config import Config

    app = QApplication.instance() or QApplication([])  # noqa: F841 - 必须活着
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)

    messages: list[tuple[str, tuple, dict]] = []
    yes = QMessageBox.StandardButton.Yes
    no = QMessageBox.StandardButton.No

    def _record(name: str):
        def _box(*args, **kwargs):
            messages.append((name, args, kwargs))
            if name == "question":
                return yes if confirm else no
            return yes  # information / warning 的返回值无意义

        return _box

    for name in ("question", "information", "warning"):
        monkeypatch.setattr(QMessageBox, name, _record(name))

    config = Config(base=str(tmp_path / "cfg"))
    config.set("scale", 3.0)
    config.set("character", "some-other-character")
    config.save()
    dialog = ModernSettingsDialog(config, include_ai=False)
    return dialog, config, messages


def test_settings_page_button_resets_and_the_dialog_cannot_revert_it(tmp_path, monkeypatch):
    """从真实设置窗口点「恢复默认设置」：默认值落盘，且窗口不会顺手把旧值写回去。

    覆盖本功能最容易前功尽弃的一环：`ModernSettingsDialog` 的三条关闭路径
    （`_save` / `reject` / `closeEvent`）都会拿**控件当前值**调 `_write_config()`
    写回配置。重置后控件里还是旧值（`scale=3.0`），若不在关闭前拦住，用户随手一关
    就把刚恢复的默认值覆盖回去——"点了没反应"原样复现。所以这里断言的是磁盘上的
    最终状态，而不只是 `reset_to_defaults` 的返回值。
    """
    from PySide6.QtWidgets import QDialog, QMessageBox, QPushButton

    from pet.modern_settings_dialog import SettingRow

    dialog, config, messages = _build_dialog(tmp_path, monkeypatch, confirm=True)
    try:
        assert dialog.findChild(SettingRow, "settingRow_restore_defaults") is not None, \
            "「常规」页上没有「恢复默认设置」行"

        button = dialog.findChild(QPushButton, "restoreDefaultsButton")
        assert button is not None, "按钮不存在或 objectName 不是 restoreDefaultsButton"

        button.click()

        # 确认框：破坏性操作必须默认落在「否」，且文案写明保留项
        kind, args, _kwargs = messages[0]
        assert kind == "question"
        # args = (parent, title, text, buttons, defaultButton)
        assert args[4] == QMessageBox.StandardButton.No, "二次确认的默认按钮不是「否」"
        assert "自定义台词" in args[2] and "角色名" in args[2], "确认文案没列保留项"

        # 关键断言：磁盘上的最终状态，不是内存里的中间值
        restarted = Config(base=str(tmp_path / "cfg"))
        assert restarted.get("scale") != 3.0, "重置被对话框关闭时的回写覆盖了"
        assert restarted.get("character") != "some-other-character"
        assert dialog.result() == QDialog.DialogCode.Accepted, "重置后窗口没有关闭"

        # 再补一刀：哪怕之后又走一次 close()，也不许把控件旧值写回去
        # （钉住 confirm_and_reset 里置 _saved_via_button 的意图）。
        dialog.close()
        assert Config(base=str(tmp_path / "cfg")).get("scale") != 3.0, \
            "重置后另一次 close() 又把控件旧值写回了磁盘"
    finally:
        dialog.deleteLater()


def test_settings_page_button_cancel_changes_nothing(tmp_path, monkeypatch):
    """二次确认里选「否」：配置一个字节都不动，窗口也不关。"""
    from PySide6.QtWidgets import QPushButton

    dialog, _config, _messages = _build_dialog(tmp_path, monkeypatch, confirm=False)
    try:
        dialog.findChild(QPushButton, "restoreDefaultsButton").click()

        current = Config(base=str(tmp_path / "cfg"))
        assert current.get("scale") == 3.0, "取消确认后配置被改了"
        assert current.get("character") == "some-other-character"
        assert dialog.result() == 0, "取消后窗口不该关闭"
    finally:
        dialog.deleteLater()


def test_reset_touches_only_the_target_config_file(tmp_path):
    """重置只写它自己那个配置文件，副槽（config-slot-N.json）一个字节都不动。

    多开时每个槽位有自己的配置文件，而槽位里的设置是用户对那条子肥鱼单独调过的
    （``slot_manager.seed_slot_config_from_main`` 只在 ``user_customized`` 为假时
    才从主配置刷新）。「恢复默认」是主设置页的动作，没有理由顺带把子肥鱼的配置
    也抹掉——那是用户没要求、也看不见的破坏。
    """
    base = str(tmp_path / "shared")
    main = Config(base=base)
    slot = Config(base=base, instance_id="slot-1")
    assert slot.path != main.path, "前提：副槽是独立文件"

    main.set("scale", 3.0)
    main.save()
    slot.set("scale", 2.5)
    slot.save()

    assert settings_reset.reset_to_defaults(main) is True

    slot.reload()
    assert slot.get("scale") == 2.5, "重置波及了副槽配置"
    main.reload()
    assert main.get("scale") != 3.0, "主配置没被重置"


def test_main_process_does_not_revert_reset(tmp_path):
    """跨进程那条路：设置进程重置落盘 → 主进程 reload 拿得到 → 主进程 save 不写回旧值。

    这才是本功能真正的失败模式，也是它和缺陷 15 同源的地方：设置页默认跑在**独立
    进程**里（``settings_process_isolation``），重置只改设置进程的内存是不够的。
    主进程若看不到，随后任何一次 ``save()``（桌宠拖一下就触发）都会把旧值写回磁盘，
    用户看到的是"点了没反应"。

    key 在这一侧走的是它本来的路：设置对话框保存时 key 进 keyring、明文不落盘，
    运行时靠 ``api_key_ref`` 取回。

    诚实标注：末两条 key 断言钉的是**用户可见结果**（"恢复默认后还能连上"），
    不是某个具体机制——变异检验发现，即使重置把 ``chat.providers`` 整个清空，
    ``_merge_chat_data`` 也会补回默认 provider 与默认 ``api_key_ref``，解析照样成功，
    这两条断言不会红。这是有意保留的：它就是本功能的验收标准，值得有一条端到端的
    断言守着"任何时候都别让用户丢 key"。
    """
    factory = Config(base=str(tmp_path / "factory"))
    base = str(tmp_path / "shared")
    main = Config(base=base)
    settings = Config(base=base)  # 独立实例 = 独立进程，只共享磁盘上的 config.json

    FakeStore.shared["provider/openai-main"] = "sk-in-keyring"
    main.set("scale", 3.0)
    main.set("character", "some-other-character")
    main.set("rx", 0.1)
    main.set("ry", 0.9)
    assert main.save() is True

    settings.reload()
    assert settings.get("scale") == 3.0, "前提：设置进程读到了主进程写下的值"
    assert main.resolve_api_key(_ProviderView(main, "openai-main")) == "sk-in-keyring"

    assert settings_reset.reset_to_defaults(settings) is True

    main.reload()
    assert main.get("scale") == factory.get("scale"), "主进程 reload 没拿到重置后的值"
    assert main.get("character") == factory.get("character")

    assert main.save() is True
    restarted = Config(base=base)
    assert restarted.get("scale") == factory.get("scale"), \
        "主进程 save 把旧值写回了磁盘——重置被静默回滚（缺陷 15 同一个坑）"
    assert restarted.get("character") == factory.get("character")
    assert restarted.get("rx") == 0.1, "运行时位置被重置波及"
    assert restarted.get("ry") == 0.9

    # 重启后（新进程）key 仍可解析：重置后的 ref 必须还在
    assert restarted.resolve_api_key(_ProviderView(restarted, "openai-main")) == "sk-in-keyring", \
        "重置后 API Key 解析不出来了（api_key_ref 被换掉或 provider 被删）"
    assert main.resolve_api_key(_ProviderView(main, "openai-main")) == "sk-in-keyring", \
        "重置后运行中的主进程解析不出 API Key"
