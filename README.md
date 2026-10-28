# Stash 简介批量翻译工具

一个给 [Stash](https://github.com/stashapp/stash) 用的零依赖本地翻译小工具：调用你自己的本地大模型（KoboldCpp），把影片库里的英文/日文简介批量翻译成中文，译文追加在原文下方，原文永远保留，随时可还原。
效果预览


<img width="439" height="511" alt="屏幕截图 2026-10-28 185005" src="https://github.com/user-attachments/assets/a01b4e1c-3274-45ec-a8dd-4c38469d7f8d" />

## 功能特性

- **批量翻译**：一次命令翻译全库，也支持先试翻几条看效果
- **原文保留**：译文以 `【中文简介】` 标记追加在原文后，不破坏原始数据
- **单片操作**：通过 oshash 值单独重翻或清除任意一部影片，不必动全库
- **增量翻译**：只翻没翻过的，新增影片后再跑一次只翻新增的
- **断点续翻**：中途关机/退出，重新运行自动接续，不会重复
- **操作前确认**：所有会修改数据的操作都先显示现状，输入 Y 才执行，直接回车一律取消
- **一键还原**：`--restore` 恢复所有原始简介
- **自动备份**：每次批量翻译前自动备份数据库文件
- **零依赖**：只需要 Python，不用装任何第三方包
- **本地运行**：所有数据不出你的电脑

## 环境要求

1. **Python 3.8+**（[python.org](https://www.python.org/downloads/) 下载，安装时勾选 "Add Python to PATH"）
2. **[KoboldCpp](https://github.com/LostRuins/koboldcpp/releases)**（下载 `koboldcpp.exe` 或 `koboldcpp_nocuda.exe`）
3. 一个**翻译向的本地模型**（GGUF 格式），推荐：
   - [Murasaki-Translator](https://huggingface.co/DavidNguyen03/Murasaki-Translator)——整段翻译完整、质量好（本项目默认测试用），下载其中的 `Murasaki-8B-v0.2-Q5_K_M.gguf`
   - [Sakura-GalTransl](https://huggingface.co/SakuraLLM)——日文翻译质量高

   ⚠️ 不推荐用字幕类模型（如 `ja2zh` 系列）：它们被训练成"一大段压成一行"，会把整段简介缩成一句话。
4. [Stash](https://github.com/stashapp/stash)——本工具在 **v0.31.1**（Windows）上开发测试，其他版本未验证

> ❗ 不支持云端 API（OpenAI / 智谱等）：此类接口会拒绝成人内容文本（HTTP 400），请使用本地模型。

## 快速开始

1. 把 `translate_details.py` 下载到一个文件夹（比如 `E:\Stash`）
2. 用 KoboldCpp 加载你的翻译模型，启动服务（默认端口 `5001`）
3. 运行 `python translate_details.py --check`——首次运行会自动生成 `config.ini`
4. 用记事本打开 `config.ini`，主要检查 `[stash]` 下的 `db_path` 是否指向你的 Stash 数据库（如 `E:\Stash\stash-go.sqlite`）
5. 先试翻 3 条：
   ```
   python translate_details.py --limit 3
   ```
6. 打开 Stash 随便点开一部刚翻的影片，确认简介里出现 `【中文简介】` 和译文
7. 满意后跑全库：
   ```
   python translate_details.py
   ```

## 命令说明

| 命令 | 作用 |
|---|---|
| `python translate_details.py --check` | 只统计不翻译：多少条待翻/已翻（只读，不询问） |
| `python translate_details.py` | 翻译全部待翻的（增量、断点续翻，开始前需确认） |
| `python translate_details.py --limit 3` | 只翻 3 条试水 |
| `python translate_details.py --restore` | 还原所有原始简介、清空翻译记录（开始前需确认） |
| `python translate_details.py --oshash <值>` | 单独重翻这一部（只影响这一部，开始前需确认） |
| `python translate_details.py --oshash <值> --clear` | 只清除这一部的翻译、恢复原版 |
| `python translate_details.py --oshash <值> --check` | 查看这一部的翻译状态和译文预览（只读） |

运行时每行输出影片的 **oshash 值**，粘贴到 Stash 搜索框即可定位到具体影片。

## 单独操作某一部影片

觉得某部影片翻得不好？先在 Stash 搜到这部片子，复制它的 oshash 值，然后：

```
:: 先看看现在的翻译状态（只读）
python translate_details.py --oshash 16位值 --check

:: 不满意？一条命令重翻（自动清除旧译文 -> 重新翻译 -> 写回）
python translate_details.py --oshash 16位值

:: 还不满意？再跑一次就换一个翻译结果；想彻底恢复原版：
python translate_details.py --oshash 16位值 --clear
```

- 单片**重翻**需要 KoboldCpp 在运行；单片**清除**只需要 Stash 在运行
- 单片操作不影响其他影片，也不做全库备份——原文永远保留在 `【中文简介】` 标记之前，清除是非破坏性的，可反复操作

## 操作确认（Y/N 安全锁）

所有会修改数据的操作（批量翻译、试翻、还原、单片重翻、单片清除）动手前都会：

1. 先显示库内现状或该影片的原文/译文预览
2. 停下来等你确认：输入 `Y` 回车才执行；直接回车或输入其他任何内容一律取消，不做任何修改

只有 `--check`（纯只读）不询问。

## 工作原理（简）

- 通过 Stash 的 GraphQL 接口读取/写回简介
- 译文以 `【中文简介】` 标记追加——这个标记同时是"已翻译"的判断依据，所以增量、续翻、还原都可靠
- 每条译文**等模型完整返回后一次性写回**，中途退出不会写坏数据；启动时还会自动修复"空译文"病态数据
- KoboldCpp 会自动把思维链（reasoning）分离到独立字段，不会混进译文

## 常见问题

**Q：为什么用云端 API 会报 400？**
大厂接口的内容审核会拒绝成人文本。请使用本地模型。

**Q：翻译出来只有一句话？**
你用的是字幕类模型（如 ja2zh 系列），它把整段缩译了。换 Murasaki / Sakura 这类段落翻译模型。

**Q：每次批量翻译生成的 stash-go.backup-xxx.sqlite 是什么？**
翻译前的数据库完整备份，出问题时可以把 Stash 回滚到那一刻。确认翻译结果没问题后，旧备份可手动删除，只留最新一份即可。

**Q：速度很慢？**
纯 CPU 跑 8B 模型每条约 1~2 分钟，全库需要数小时，属正常现象。有 NVIDIA 显卡的话用 `koboldcpp.exe`（CUDA 版），AMD 显卡用 `koboldcpp_nocuda.exe`（Vulkan）都会快很多。

**Q：中途退出/断电了怎么办？**
重新运行即可，自动从断点继续。

**Q：想翻成繁体/英文？**
改 `config.ini` 里的 `target_lang`。

## 注意事项

- 本工具面向 **18+ 成人内容库的个人本地使用**，请遵守当地法律法规
- 翻译前会自动备份数据库，但仍建议定期自行备份重要数据
- `config.ini`、`translated_ids.json`、`original_details.json` 含个人库信息，**不要分享给他人**

## 鸣谢

- [KoboldCpp](https://github.com/LostRuins/koboldcpp)——本地模型推理服务
- [Stash](https://github.com/stashapp/stash)——最好的本地媒体管理器

## License

[MIT](LICENSE)
