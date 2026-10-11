# PR 报告：设置页「恢复默认设置」入口（issue #220）

> **基线**：`ee699c7`（Merge PR #233 `feature/agent-link-subtraction`，即当前 `upstream/main`）
> **分支**：`feat/220-restore-defaults`　**日期**：2026-10-11
> **范围**：4 个文件（实现 2、测试 1、文档 1）
> **关联**：[issue #220](https://github.com/MerZlin/dsh-pet-indesktop/issues/220)；
> 门禁依据 [`SETTINGS-CHANGE-GATES.md`](SETTINGS-CHANGE-GATES.md)；
> 同源缺陷见 [`PR-REPORT-FULL-REVIEW-FIXES-2026-10-03.md`](PR-REPORT-FULL-REVIEW-FIXES-2026-10-03.md)
> 里的「配置显式 null」一条（本报告称「缺陷 15」）

## 一、核心特性

设置窗口「常规」页底部新增一个「恢复默认设置」入口：二次确认后把所有**偏好类**
设置写回出厂默认并落盘，然后关闭设置窗口。

反馈源是 B 站 v3.1.0 发布视频评论区的一位用户（BV1478X6iEAN）：他把 chat 的 API
地址填成了本机地址——产品里 `pet/self_talk_voice.py` 的 `DEFAULT_SERVER` 就是
`http://127.0.0.1:9880`，抄错字段完全可复现。维护者当时手工引导他填回
`https://api.deepseek.com` / `deepseek-v4-flash`，**而这两条逐字就是
`config._default_chat_data()` 的出厂默认值**——本功能要自动化掉的正是这件事。

| # | 能力 | 说明 |
|---|---|---|
| 1 | 一键回出厂 | 「常规」页末尾一个按钮，二次确认后全量重置偏好类键 |
| 2 | 保住不可重建的东西 | 自写台词、角色别名、快捷启动清单、右键菜单排布、音乐软件与 pnpm 路径、API Key、多开槽位配置一律不碰 |
| 3 | 不动运行时几何 | 桌宠当前位置/所在屏/朝向不重置（**为什么**见 §三第 2 条） |
| 4 | 关窗不反弹 | 重置后立即 `accept()` 关窗，杜绝「关窗时把控件旧值写回去」把重置抹掉 |

**红线 / 不变量**：

1. `CollisionIpcSession` / 多开槽位 / 域认领等既有语义不变；
2. `config.json` 的键集与出厂键集保持同构（129 个顶层键，差集为空）——重置后
   任何走 `reload()` 的代码路径看到的都是**完整**配置，不是半个；
3. 重置**只写非 null 的完整默认值**，不写 `null`、不删键（§三第 1 条）。
   这条不变量由 `test_reset_never_writes_a_key_reload_would_ignore` 全量键集守着。

## 二、修改文件说明

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `pet/settings_reset.py` | +234 / −0（新） | 重置的全部逻辑与全部 UI 构造 |
| `pet/modern_settings_dialog.py` | +5 / −0 | 只加一行接线（+ 一行 import + 3 行注释） |

**`pet/settings_reset.py`（新文件）** 分四块：

- `default_config_data()`：用一个**空临时目录**造一份全新 `Config` 取它的 `data`
  作为出厂基准。`Config` 的默认值内联在 `__init__` 里（`config.py:717` 起的一大段
  字面量 + 散落的模块常量），没有独立工厂函数。三条安全性已核：源码运行下
  `_migrate_legacy_config` 第一行就 return；`_seed_slot_config_from_main` 只在设了
  `instance_id` 时执行（这里不设）；空目录下 `__init__` 末尾的 `reload()` 直接
  return。实测全新 `Config.data` 与用户真实 `config.json` 的键集差集为空。
- `reset_to_defaults(config)`：拷贝出厂数据 → 把 `_UNTOUCHED_KEYS` 的现值盖回去 →
  `_carry_chat_secrets` 搬走内存里的明文 API Key → 整体替换 `config.data` →
  `config.save()`。**为什么整体替换而不是逐键赋值**：逐键赋值要求维护一份"哪些键
  属于偏好"的白名单，那份白名单会和 `Config` 的键集持续漂移；反过来只维护
  **不碰**的小集合（10 个键）就够了，新增键自动落在"会重置"这一侧——而"新设置
  默认该被恢复"正是这个功能想要的默认方向。
- `settings_rows(dialog)` / `confirm_and_reset(dialog)`：见 §三第 3 条。

**`pet/modern_settings_dialog.py`（+5 / −0）** 只加三处：

```python
from . import settings_reset                     # import 区一行
# ... 在 _rebuild_domain_navigation 内 ("窗口与系统", ...) 之后：
                # 「恢复默认」放在「常规」域末尾（issue #220）：配置被改坏时的兜底
                # 自助入口。行与控件在 settings_reset 构建（行数预算原因），
                # 且创建时机晚于 _rebuild_domain_navigation 的行快照，不需要 claim。
                ("恢复默认", settings_reset.settings_rows(self)),
```

**为什么控件不建在对话框里**：`MODERN_SETTINGS_DIALOG_PY_LINE_BUDGET = 2407`
（`tests/test_architecture.py:221`），改前实测 2394 行，只剩 13 行余量。照
`settings_music.browser_media_rows` 的先例（那个文件最后一次改动正是把设置行搬出去
压预算），把行与控件的构造整体放进 `settings_reset.py`。**没有**为了达标压缩行宽
或合并语句——该预算的注释明确禁止反向优化。改后 2399 行，仍在预算内，未上调预算。

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_settings_reset.py` | +468 / −0（新） | 11 个用例，见 §六 |

按行为切片逐个红→绿：偏好键回默认 → 保留清单 → 大小与角色 → 运行时几何 →
不写 reload 会忽略的键 → API Key → config version → 槽位隔离 → 跨进程不反弹 →
设置页 seam 两条路径（确认 / 取消）。

### 未改动（故意没动）

- `pet/config.py`：**没有**新增配置键，**没有**往 `_NULL_ACCEPTING_KEYS` 加键，
  也没动 `reload()`。这是刻意的——见 §三第 2 条。
- `tests/test_config_reload_null.py`：把 `_NULL_ACCEPTING_KEYS` 钉成恰好一个键的
  守卫断言原样保留。
- `pet/settings_menu_layout_editor.py`：它自带的「恢复默认布局 / 恢复默认名称与图标」
  是**局部**重置，与本功能是"单点修 vs 整体回出厂"两层，不合并。

## 三、实现要点

### 1. 机制只能是「写完整默认值」，不能写 null、不能删键

`Config.reload()` 对白名单内每个键的处理是：

```python
if key not in raw:                                    continue   # 删键 → 保留内存现值
if value is None and key not in _NULL_ACCEPTING_KEYS: continue   # 写 null → 同样保留
self.data[key] = value
```

而 `_NULL_ACCEPTING_KEYS` 只有 `context_menu_layout` 一个键。实测（对 124 个白名单
键）：**写 null 生效 1/124，删键生效 0/124，写完整默认值生效 124/124**。所以重置
写的是完整出厂值。那个唯一吃 null 的键本身也在保留清单里，于是"本模块一个 null
都不写"是恒真的，不靠"恰好只有它能吃 null"这种巧合。

### 2. 位置 / 朝向不算「设置」——这一条是实测推翻了初始设计

初版方案把 `rx` / `ry` / `screen_name` 也重置。做跨进程验证时发现它**是坏的**：

这三个键的出厂默认是 `None`（= "还没摆过位置"），重置把它们写成 `null` 落盘后，
**正在运行的桌宠进程会忽略它、保留旧位置**，随后用户拖一下桌宠
（`save_position`）就把旧位置写回磁盘——重置被静默回滚。实测：盘上 `rx=null`，
主进程 `reload()` 后仍是 `0.1`，`save()` 后盘上又变回 `0.1`。这就是缺陷 15 的
同一个坑。

要真正重置它们，只能往 `_NULL_ACCEPTING_KEYS` 加键，而 `tests/test_config_reload_null.py`
把那个集合钉成恰好一个键。**不值得**：位置本身已经自愈——
`window_placement.restore_position` 与 `overlay_shell._restore_position` 都把坐标
钳回可用区、副屏掉线时回退当前屏，"桌宠跑到屏幕外"并不靠重置兜底。而 issue #220
的原话是恢复默认**设置**；`scale` 与 `character` 在外观页里确实是可以改的设置，
它们照重置。所以：`rx` / `ry` / `screen_name` / `facing` 进 `RUNTIME_GEOMETRY_KEYS`，
一个都不碰（`facing` 与它们同属"每窗运行时状态"——
`slot_manager._SEED_EXCLUDE_KEYS` 把这四个键一并排除在配置继承之外，理由同款）。

### 3. 重置完必须立刻关窗，否则用户的下一次保存会把重置抹掉

`ModernSettingsDialog` 的保存语义是"随手关也保存"：`_save` / `reject` /
`closeEvent` 三条路径都会拿**控件当前值**调 `_write_config()` 写回配置（设计如此）。
重置之后控件里装的还是旧值，窗口若继续开着，用户下一次保存或关窗就把刚恢复的
默认值覆盖回去——正是本功能要修的那种"点了没反应"。

所以 `confirm_and_reset` 主动 `accept()` 收尾：它走 `done(Accepted)`，不经过上述
三条路径中的任何一条。同时置位既有的 `_saved_via_button` 闸门——`accept()` 本身
不回写，但任何**后续**的 `close()` / `reject()` 若闸门没关，就会拿控件旧值调
`_write_config()`。

> 这一步不是防御性摆设：变异检验发现删掉 `dialog._saved_via_button = True` 时
> 全部测试仍绿（`accept()` 走 `done()` 隐藏窗口，不触发 `closeEvent`）。测试里
> 补了一次 `dialog.close()` 断言之后重做变异，**删掉该行即红**，才留下它并写下
> 这句注释。

### 4. `context_menu_layout` 划进保留清单（超出 issue 字面范围的一次判断）

它出厂默认恰为 `None`，是唯一吃 null 的键。留它在重置范围内会毁掉用户手改的菜单
排布/别名/图标，而**没有任何恢复路径**——`settings_menu_layout_editor.reset_default()`
装载的是**内置**默认布局，不是用户自己那份。反过来，保留它不损失兜底能力：布局
非法时 `_save` 本来就会拒绝落盘（`menu_validation.source == "fallback"`），
"菜单布局被改坏"这个场景不存在。按本模块自己的口径（「资产」= 用户创作的东西），
它属于资产侧。取舍依据是"用户会失去什么、能不能拿回来"这一对不对称性。

### 5. API Key 必须手工搬过去

key 的出厂默认是**空串**；若连 key 一起重置，用户"恢复默认"之后照样连不上——
而维护者当时给那位用户的第三条解决办法恰恰是"自己在 DS 官网创建一个 key"。
`_redacted_data()` 本就不把明文 key 写盘，所以整体替换 `config.data` 会连内存里
那份 key 一起换掉（keyring 不可用的路径），`_carry_chat_secrets` 显式搬过去。
只对**两边都有的 provider** 生效：重置会删掉用户自建的 provider，给它单独留一份
key 没有意义，而多出来的"幽灵 provider"还会破坏 `_merge_chat_data` 的
`active_provider` 回退（它按 providers 的键集合挑活跃项）。

## 四、性能分析

**方法（可复现）**：基线 `ee699c7` + 本分支，均在本机真实环境跑：

```bash
python .scratch/issue-220-restore-defaults/bench_reset.py       # 成本 / 资源增量 / 端到端
python .scratch/issue-220-restore-defaults/bench_reset_ab.py    # 窗口构建 A/B 交替采样
python .scratch/issue-220-restore-defaults/bench_reset_mem.py   # 驻留与峰值
```

脚本与原始输出存在 `.scratch/issue-220-restore-defaults/`（该目录被 `.gitignore`
覆盖，不进提交；提交的是本报告里的数字）。脚本把配置目录用 `APPDATA` 重定向到
临时目录（在 `import pet.config` **之前**），全程不碰用户真实配置。

**环境**：Windows 11 家庭中文版 build `10.0.22631`（`platform.release()` 报 `10`）
／ AMD64 Family 25 Model 97 Stepping 2 / 32 逻辑核 ／ CPython 3.11.9 ／
PySide6（本机安装版）／ 样本量 N=200（窗口构建 N=12×2 交替）。

| 指标 | 实测 | 归属 |
|---|---|---|
| `default_config_data()`（空临时 `Config` + deepcopy） | p50 **0.448 ms** / p95 0.493 / max 0.947（N=200） | 新增路径 |
| 同上 + `config.save()`（**一次点击的全成本**） | p50 **1.348 ms** / p95 1.495 / max 1.866（N=200） | 新增路径 |
| `settings_rows(dialog)`（建 1 行 + 1 按钮） | p50 **0.079 ms** / p95 0.119 / max 0.406（N=200） | 新增，窗口构建期一次性 |
| 连续 50 次重置总耗时 | **66.6 ms**，单次均 1.332 ms | 新增路径（极端重复） |
| 设置窗口构建：有该行 vs 无该行（A/B/A/B 交替） | **+4.54 ms / −3.24 ms**（两次独立运行**符号相反**） | 对照 |
| 两组各自内部离散度（max−min） | A 30.21 ms ／ B 67.60 ms | 对照 |
| 对照：一次普通 `config.save()`（用户每改一个设置、桌宠每挪一次都要走） | p50 **0.838 ms** / p95 0.911（N=200） | 既有 |
| `config.data` 深度占用（含全部子对象） | 24.56 KiB → 24.56 KiB（**+0.00 KiB**） | 内存 |
| `config.data` 序列化字节 / 顶层键数 | 6281 B → 6281 B（**+0**） ／ 129 → 129 | 内存 |
| 单次调用 tracemalloc 峰值 / 纯临时 | 65.96 KiB ／ **56.17 KiB**（随调用结束释放） | 内存 |
| 连点 500 次后 `config.data` 深度占用 | +0.00 KiB | 内存 |
| 新线程 / 新建 socket | **0 / 0**（`threading.active_count()` 1 → 1） | 资源 |
| 落盘次数 | **1 次**（`config.save()`：PID 临时文件 + `atomic_replace_with_retry`） | 磁盘 |

**结论**（逐条回答模板要求）：

1. **稳态开销有没有变化**：窗口打开时一次性多花 `settings_rows` p50 **0.079 ms**
   （建 1 个 `SettingRow` + 1 个 `QPushButton`）。之后**稳态零开销**——本功能没有
   定时器、没有常驻对象、没有回调注册，不做任何轮询；它在 `_rebuild_domain_navigation`
   里被构造一次，之后只是静静地待在那儿等着被点。
2. **新增路径的绝对成本与触发频率**：一次点击 = `default_config_data()`
   （p50 0.448 ms，纯计算 + 一次 `TemporaryDirectory` 创建/删除）+ `config.save()`
   （p50 约 0.84 ms，与用户改任何一个设置所付的成本同量级）。合计 **p50 1.348 ms**，
   p95 1.495 ms。**触发频率 = 每次点击 1 次**，不是每次启动、不是每帧、不是每条动画。
   注意 `default_config_data()` 是本实现里的最贵单项——它为了拿到出厂基准而要造一个
   临时 `Config`；1 毫秒级、且只在点击时发生，接受。
3. **有没有新的系统调用 / 网络 / 磁盘 / 线程**：线程 0 新增（1 → 1）；socket 0 新增
   （探针把 `socket.socket` 换成计数子类，全程 0 次构造）；网络 0；磁盘 **1 次写**
   （走既有 `config.save()` 路径：PID 后缀临时文件 → `atomic_replace_with_retry`），
   另有 1 次临时目录创建+删除（`default_config_data`）。**不新增**任何线程、连接、
   定时器或轮询。
4. **内存与缓存有没有增长**：`config.data` 深度占用 +0.00 KiB、序列化字节 +0、顶层
   键数 129 → 129。单次调用有 56.17 KiB 的**一次性**临时分配（一份出厂配置的深拷贝
   + 序列化缓冲），随调用返回释放；连点 500 次的驻留增量仍是 **+0.00 KiB**，不累积。
   `tracemalloc` 报的 +13.6 KiB 净增量里，前几项是 `copy.deepcopy` 的 memo 与
   `json` 编码器自身的常驻对象，一次性的。

**窗口构建 A/B 的判读（诚实标注）**：两次独立运行分别给出 **+4.54 ms** 与
**−3.24 ms**——符号相反，而两组各自的内部离散度是 30–68 ms（A/B 各 12 次交替采样、
先热身）。也就是说这 1 行在 ~300 ms 的窗口构建里**测不出**：0.079 ms 的孤立成本本就
淹没在运行间波动里。这里给不出更窄的置信区间，不把它包装成"降低了 1.06%"。

## 五、实机运行记录

**本机真实环境**（Windows 11 22631，真实平台插件，**不是** offscreen、**不是** CI）。
脚本：`.scratch/issue-220-restore-defaults/probe_reset_ui.py`（浅色）与
`probe_reset_dark.py`（深色）。两者都在 `import pet.config` **之前**把 `APPDATA`
重定向到临时目录，所以**全程没有读写用户真实配置**；跑完删临时目录。

### ① 根因/前提的现场复现：位置重置是坏的

同源缺陷的现场证据（这是推翻初版设计的实测，不是推断）：

```
盘上 rx=null → 主进程 reload() 后 rx 仍是 0.1 → save() 后盘上又变回 0.1
```

即：写 null 的重置会被正在运行的进程静默忽略，并被下一次拖拽写回。这直接决定了
`rx` / `ry` / `screen_name` / `facing` 进保留清单（§三第 2 条）。

### ② 用户可见行为的确认：两条路径都驱动过真实模态框

`probe_reset_ui.py` 用真实的 `ModernSettingsDialog`（`standalone=True`）、真实的
模态 `QMessageBox`（通过 `QApplication.activeModalWidget()` 抓取后截图再按真实按钮），
真实落盘，并断言"新进程视角"（另起一个 `Config` 读盘）：

```
配置目录 : C:\Users\JanG\AppData\Local\Temp\reset-ui-1l8osv6n\dsh-pet-standalone\config.json
  改坏后: scale=1.5 character='some-other-character' on_top=False
截图 compact  实际宽度  720 滚动条数=1 -> 01-常规页底部-compact.png
截图 standard 实际宽度  900 滚动条数=1 -> 01-常规页底部-standard.png
截图 wide     实际宽度 1320 滚动条数=1 -> 01-常规页底部-wide.png
按钮     : <QPushButton name="restoreDefaultsButton"> 文案='恢复默认设置…' accessibleName='恢复默认设置'
  [shot] 02-确认框 标题='恢复默认设置' 默认按钮='&No'
  选否之后: scale=1.5 character='some-other-character' on_top=False
对话框还开着吗: True
  [shot] 03-确认框-选是 标题='恢复默认设置' 默认按钮='&No'
  [shot] 04-完成提示 标题='恢复默认设置' 默认按钮='OK'
  选是之后: scale=0.72 character='shenshen' on_top=True
对话框还开着吗: False
新进程视角: scale=0.72 character='shenshen' on_top=True
```

用户可见行为逐条对上：**选否**→配置一个字节不动、窗口不关（"本该失败"的那条路径）；
**选是**→ `scale 1.5→0.72`、`character→shenshen`、`on_top False→True`、窗口关闭、
且**另起一个进程读盘看到的也是重置后的值**（不是只在内存里）。

深色主题与键盘可达性（`probe_reset_dark.py`，`context_menu_appearance.theme` 固定
`dark` 后建窗）：

```
主题期望 : dark
控件实际 : 'dark' settingsDark=True
截图 compact  宽度  720 -> 05-深色-常规页底部-compact.png
截图 standard 宽度  900 -> 05-深色-常规页底部-standard.png
截图 wide    宽度 1320 -> 05-深色-常规页底部-wide.png
焦点策略 : <FocusPolicy.StrongFocus: 11>  (StrongFocus=11)
setFocus 后 hasFocus = True
页面内可见可聚焦 QPushButton 数: 1
第 10 次 Tab 到达恢复默认按钮
Tab 链前 12 项: ['saveAndExit', 'settingsSearch', 'settingsSidebar', 'ToggleSwitch',
 'ToggleSwitch', 'ModernSelect', 'ToggleSwitch', 'ToggleSwitch', 'ToggleSwitch',
 'restoreDefaultsButton']
```

截图存于 `C:\Users\JanG\Desktop\issue220-截图\`（10 张：浅色 3 档宽度 + 确认框 +
确认框选是 + 完成提示 + 深色 3 档宽度 + 深色按钮焦点态）。

### ③ 边界与失败路径

- **确认框选否**：配置零改动、窗口保持打开（上面 ② 里的一对 `选否之后` / `对话框还开着吗: True`）。
- **默认按钮落在"否"**：探针打印 `默认按钮='&No'`——这是破坏性操作，不让回车直接
  触发。测试里另有一条断言钉住 `args[4] == StandardButton.No`。
  （早期版本断言写成了 `args[3]`，实得 `Yes|No`，是测试写错、不是产品错，已修。）
- **写入失败路径**：`confirm_and_reset` 在 `reset_to_defaults` 返回 False 时弹
  `warning` 并**不关窗**。这一条**没有**实机跑过（没有可靠办法在本机让
  `config.save()` 确定性失败），是本次交付的诚实缺口，见 §七。

### ④ 无法自动验证的能力

- **本机确实`python -m pet` 起真宠、从托盘打开设置点这个按钮**：没做。理由是
  它会在这台日常使用的机器上真实弹出桌宠窗口，而本轮验收的目标（真实 Qt 平台插件
  + 真实模态框 + 真实落盘 + 真实键盘事件）已由上面 ② 的三份输出覆盖；差异只在
  "设置窗口是从托盘哪个入口打开的"，而入口不影响本功能。**结论是"没做，且知道
  没做"**，不用沉默代替。
- **运行中的桌宠进程是否会应用这次重置**（`settings_process_isolation` 下设置页跑
  在独立进程）：走的是既有的 `QFileSystemWatcher` → 300 ms 去抖 →
  `Config.reload()` → `_apply_external_config_change()` 链路，该链路已有测试
  （`tests/test_settings_process_isolation.py::test_config_watcher_reloads_and_applies_external_change`）。
  本改动**没有**碰这条链路；而"重置写下的值能不能被 `reload()` 采纳"这一环，由
  `test_reset_never_writes_a_key_reload_would_ignore` 全量键集钉住、并由
  `test_main_process_does_not_revert_reset` 做跨进程端到端验证。
- **确认框里 `Yes` / `No` 显示为英文**：真实观察到的现象。原因是本仓库**没有**
  安装任何 `QTranslator`（`pet/` 全树 grep `QTranslator` / `installTranslator` 零命中），
  所以产品里**所有** `QMessageBox` 的标准按钮都是英文。这是既存行为、非本改动引入，
  且与最近的同类确认框一致（`settings_menu_layout_editor.py:730` 的
  `Yes | Cancel`、`agent_link.py:2784`/`2802` 的 `Yes | No`）。**故意不单独把这一个
  框改成中文**——那会让它成为 20 多个消息框里唯一的异类。若要统一改，是另一个工单。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `python -m ruff check .` | **All checks passed!** |
| 空白/冲突 | `git diff --cached --check` | 退出 0（干净） |
| 聚焦 | `pytest -q tests/test_settings_reset.py` | **11 passed** |
| 相关族 | `pytest -q tests/test_architecture.py tests/test_config_{domains,instance,key_migration,reload_null,schema}.py tests/test_settings_{reset,interaction_tabs,and_resources,process_isolation}.py` | **151 passed** |
| 受影响时序族满载 3 遍 | 8 个 CPU 烧机进程（32 逻辑核里打满 8）+ `pytest -q tests/test_settings_process_isolation.py tests/test_settings_reset.py` ×3 | **45 passed / 45 passed / 45 passed**（5.81s / 5.86s / 7.38s） |
| 全量 | `python -m pytest -q`（`QT_QPA_PLATFORM=offscreen`） | **4315 passed, 14 skipped, 17 warnings, 310.45s** |
| 架构红线 | `tests/test_architecture.py::MODERN_SETTINGS_DIALOG_PY_LINE_BUDGET` | 2399 / 2407 行，**在预算内** |
| 断言有效性（变异检验） | 临时改动源码重跑 | 两处，见下 |

**变异检验（证明断言能区分新旧实现）**：

1. 删掉 `confirm_and_reset` 里的 `dialog._saved_via_button = True`：
   - 只补测试前**全部绿**（`accept()` 走 `done()`，不触发 `closeEvent`）——说明原断言
     没覆盖它；
   - 补上 `dialog.close()` 断言后重做变异 → `test_settings_page_button_resets_and_the_dialog_cannot_revert_it`
     **红**。该行至此才有测试钉住，注释里也据此写明"去掉本行即红"。
2. 从 `PRESERVE_KEYS` 删掉 `"context_menu_layout"` →
   `test_reset_preserves_user_assets_and_machine_paths` **红**
   （`assert None == {'nodes': [...]}`），确认新增的保留项是承重的、不是装饰。

**新增测试一览**（`tests/test_settings_reset.py`，11 条）：

| 用例 | 钉住什么 |
|---|---|
| `test_reset_restores_preference_keys_to_factory_default` | 数值/布尔偏好键逐键回出厂 |
| `test_reset_preserves_user_assets_and_machine_paths` | 台词/别名/清单/菜单排布/路径不被抹 |
| `test_reset_restores_size_and_character` | 大小与角色属于「设置」，照重置 |
| `test_reset_leaves_runtime_geometry_alone` | 位置/屏/朝向一个都不动（附完整理由） |
| `test_reset_never_writes_a_key_reload_would_ignore` | 全量键集：落盘的桶 `reload()` 必须都采纳 |
| `test_reset_keeps_chat_api_key` | keyring 不可用时内存里的 key 不丢 |
| `test_reset_keeps_config_version` | `version` 留在当前值（刻意不过 `reload()`） |
| `test_reset_touches_only_the_target_config_file` | 副槽 `config-slot-N.json` 零改动 |
| `test_main_process_does_not_revert_reset` | 跨进程：重置不被下一个 `save()` 回滚 |
| `test_settings_page_button_resets_and_the_dialog_cannot_revert_it` | 设置页 seam：落盘终态 + 关窗不反弹 + 默认按钮为否 |
| `test_settings_page_button_cancel_changes_nothing` | 选否：零改动、不关窗 |

测试替身说明：本文件用内存假 keyring（口径照 `tests/test_config_key_migration.py`
的同名替身）。**必须**替掉真实 keyring——`_migrate_plaintext_keys_to_keyring` 在
`reload()` 末尾会把内存里的明文 key 写进 keyring 并从 `self.data` 弹掉；若用真实
实现，本地（有 keyring）会往用户本机凭据管理器（service `dsh-pet-standalone`、
ref `provider/openai-main`，正是真实条目）写入测试值，且与 CI
（`PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring`）结论分叉。

## 七、已知限制与后续

1. **写入失败路径无实机验证**：`QMessageBox.warning` 那条分支只由单测覆盖，没有
   在本机把 `config.save()` 弄失败过。要覆盖它需要造磁盘/权限故障，代价高于收益。
2. **未在真机上从托盘入口点过按钮**（§五 ④），理由与替代证据已写明。
3. **`Yes` / `No` 英文按钮**是仓库级的既存现象（无 `QTranslator`），本改动不单独修。
4. **多开子肥鱼的配置不重置**：每个槽位有自己的 `config-slot-N.json`，本入口只重置
   它自己那个文件。这是刻意的（`slot_manager.seed_slot_config_from_main` 只在
   `user_customized` 为假时才从主配置刷新），有测试钉住。
5. **没做的事**：不做重置前自动备份、不做「只重置某一页」的局部重置（后者已有菜单
   排布域的两处局部重置先例，需求出现时再谈）。

## 八、风险与回滚

**影响面**：一个新文件 + 对话框里 5 行接线。没有新增配置键，因此**没有配置迁移**，
也不会产生任何需要清理的落盘状态——重置写下的就是一份合法的出厂配置。

**风险**：

- *误点导致设置丢失*：用二次确认 + 默认按钮落在「否」+ 文案逐条列出保留项来挡。
  破坏性操作里这是最强的一道，但仍无法防"用户不看文案直接点是"。
- *与既有保存语义打架*：已由 `accept()` + `_saved_via_button` 处理，且**有测试
  钉住**（删掉该行断言即红）。
- *保留清单划错*：`PRESERVE_KEYS` / `RUNTIME_GEOMETRY_KEYS` 是显式常量，改动有
  单测守着；划错的代价是"某类值被重置"，可通过往集合里加键修（不需要数据迁移）。

**回滚**：`git revert` 本提交即可——删掉新文件、撤掉对话框里那 5 行。**没有残留
状态**：功能不落任何自己的标记文件，也不新增配置键；用户在此期间已重置过的配置
保持重置后的值（那是用户主动操作的结果，不该被回滚）。撤销后
`MODERN_SETTINGS_DIALOG_PY_LINE_BUDGET` 自然回落，无需调预算。

**未跨平台验证**：本轮只在 Windows 11 真机做了 GUI 验收（`SETTINGS-CHANGE-GATES.md`
§4 要求的"至少在当前可用平台完成真实 GUI 验收"）。macOS / Linux 未验；本改动
不涉及平台分支、不涉及 `DESKTOP_SUPPORTED` 一类能力位，`settings_reset.py` 里
唯一的平台相关处是 `Config(base=tmp)` 的临时目录（`tempfile`，
`_default_base()` 只有构造用户真实配置时才走 `$APPDATA`），风险低但**未实测**。
