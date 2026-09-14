# hellopix-tdesktop-build

Hello Pix **电报独立版**的云编译流水线：把官方 tdesktop 打上 Hello Pix 的补丁，
在 GitHub 托管的 Windows runner 上编出带气泡 `[译文]` 的 `Telegram.exe`。

这个仓库只负责**编出 exe**。启动器、桥接文件、验收步骤都在主仓库的
`standalone/telegram-win/` 下（本仓库的 `tdesktop-fork/` 是那里的镜像）。

## 它编什么

补丁给官方 tdesktop v7.2.8 加一个翻译通道：收到消息后把原文 POST 到
Hello Pix 的 `/apis/translate/run`，把返回的译文以 `原文 + [译文] 译文` 的形式
渲染在同一个气泡里。

改动落在 4 个文件（`tdesktop-fork/apply.py` 会注入并校验锚点）：

| 文件 | 改动 |
|------|------|
| `lang/hello_pix_translate_provider.{h,cpp}` | 新增。读桥接文件、发 HTTP、拼 `[译文]` |
| `lang/translate_provider.cpp` | 工厂优先返回 Hello Pix Provider |
| `history/view/history_view_translate_tracker.cpp` | 按开关过滤收/发/群，并在需要时强制开启追踪 |
| `Telegram/CMakeLists.txt` | 注册两个新源文件 |

运行时的行为由启动器写在电报数据目录的 `hello-pix-bridge.json` 决定
（`apiBase` / `deviceId` / `cookies` / `engine` / `lang` / `transRecv` / `transSend` / `transGroup`）。

## 必须先配的两个仓库密钥

| 密钥 | 说明 |
|------|------|
| `TDESKTOP_API_ID` | 在 https://my.telegram.org 申请 |
| `TDESKTOP_API_HASH` | 同上 |

**为什么是硬门禁**：tdesktop 的 `telegram_options.cmake` 把 `17349` /
`344583e45741c457fe1862106095a5eb` 定义为 `TDESKTOP_API_TEST`，同一文件里的
FATAL_ERROR 原文写着：

> Your users will start getting internal server errors on login
> if you deploy an app using those 'api_id' and 'api_hash'.

也就是说用测试凭据编出来的 exe，**编译和启动都正常，但用户一登录就报内部错误** ——
从外面看和"编译失败"没有任何区别。所以没配密钥时工作流会在第 4 步（约 16 秒）失败，
而不是烧完 3 小时再给你一个不能用的包；测试凭据模式也永不上传 artifact。

## 怎么跑

```
gh workflow run build-hellopix-telegram --repo <owner>/hellopix-tdesktop-build
```

勾 `allow_test_credentials` 可以只验证"补丁编不编得过"，那种模式产物不上传、
最后一步会显式报错。

一次完整运行约 **3 小时**（公开仓库的 `windows-2025` 是 4 核；私有仓库只有 2 核，会慢一倍）。

## 三个踩过的坑（改工作流前请先读）

1. **`prepare` 必须传 `silent`。**
   `prepare.py` 遇到 `Stale` 的 stage 会 `print` 一个交互菜单并 `getch()` 死等键盘；
   CI 没有 stdin，于是一挂到步骤超时、日志里一个字都没有。

2. **msbuild 输出必须重定向到文件。**
   直接输出的话，几万行日志会把 runner 的密钥脱敏器（`SecretMasker`）压到栈溢出，
   整个作业连同两小时的编译成果一起消失，而且崩在 runner 里连日志都取不回来。

3. **三方库缓存要精确命中。**
   `actions/cache` 的 `cache-hit` 只在主 key 精确匹配时为 true；只配 `restore-keys`
   的话命中了也判 false，于是每轮都重跑 4 小时的 `prepare`，等轮到 msbuild 就被
   6 小时上限砍掉。缓存被逐出时会自动退化（前缀命中 → prepare 补齐 → 重存）。

## 目录

```
.github/workflows/build-telegram.yml   # 流水线（正文在主仓库 standalone/telegram-win/tdesktop-fork/）
tdesktop-fork/apply.py                 # 补丁注入 + 版本锚点 + 幂等校验
tdesktop-fork/hello_pix_translate_provider.{h,cpp}
tdesktop-fork/APPLY.txt                # 补丁说明与桥接契约
```

补丁正文以主仓库 `standalone/telegram-win/tdesktop-fork/` 为准，本仓库是镜像；
两边由主仓库的测试断言逐字节一致。
