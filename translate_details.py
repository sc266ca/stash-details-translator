# -*- coding: utf-8 -*-
"""
Stash 简介批量翻译工具（零依赖，无需安装任何包）
================================================
功能：把库里影片的英文/日文简介翻译成中文，
     在原简介下方附加译文（原文保留，随时可还原）。

用法（命令行运行）：
  查看库内情况:        python translate_details.py --check
  全库翻译:            python translate_details.py
  先试 3 条:           python translate_details.py --limit 3
  还原所有原简介:      python translate_details.py --restore
  单独重翻一部:        python translate_details.py --oshash <oshash值>
  只清除一部恢复原版:  python translate_details.py --oshash <oshash值> --clear
  查看某部翻译状态:    python translate_details.py --oshash <oshash值> --check

所有会修改数据的操作（翻译/重翻/清除/还原）动手前都会先显示现状，
并要求输入 Y 确认；直接回车或输入其他内容一律取消，不做任何修改。

首次运行会在脚本同目录生成 config.ini，用记事本修改后即可使用。
"""

import sys
import json
import time
import sqlite3
import configparser
import urllib.request
from pathlib import Path
from datetime import datetime

HERE = Path(__file__).resolve().parent
CONFIG_FILE     = HERE / "config.ini"
STATE_FILE      = HERE / "translated_ids.json"     # 已翻译的 ID 记录（断点续翻用）
ORIGINALS_FILE  = HERE / "original_details.json"   # 原始简介备份（还原用）

SEPARATOR = "\n\n【中文简介】\n"   # 译文接在这个标记后面，请勿修改（断点续翻依赖它）

DEFAULT_CONFIG = """\
; ============================================
;  Stash 简介批量翻译工具 配置文件
;  用记事本编辑，保存后重新运行脚本即可
; ============================================

[stash]
; Stash 的地址，一般不用改
url = http://localhost:9999
; 仅当 Stash 设置 -> 安全 里开启了 API Key 认证时才需要填，否则留空
api_key =
; Stash 数据库文件的实际位置（用于翻译前自动备份），请改成你自己的路径
db_path = C:\\Stash\\stash-go.sqlite
; 翻译前是否自动备份数据库 (true / false)，强烈建议保持 true
backup = true

[model]
; KoboldCpp 的接口地址，默认端口 5001，一般不用改
api_base = http://localhost:5001/v1
; 本地模型不需要填
api_key =
; 模型名：用 KoboldCpp 时随便填（程序会用你在 KoboldCpp 里加载的模型）
model = local

[translate]
; 翻译目标语言
target_lang = 简体中文
; 每条翻译后的停顿秒数，防止请求过快
pause_seconds = 1.0
"""

# 不走系统代理，避免本机代理软件干扰
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def load_config():
    """读取 config.ini；不存在则生成默认配置"""
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(DEFAULT_CONFIG, "utf-8")
        print(f"首次运行：已在 {CONFIG_FILE} 生成默认配置文件。")
        print("一般无需修改即可使用；如路径不同，请用记事本编辑后重新运行。")
        print()
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(CONFIG_FILE, encoding="utf-8-sig")
    g = lambda sec, key, fb: cp.get(sec, key, fallback=fb)
    return {
        "STASH_URL":     g("stash", "url", "http://localhost:9999").rstrip("/"),
        "STASH_API_KEY": g("stash", "api_key", "").strip(),
        "DB_PATH":       g("stash", "db_path", "").strip(),
        "DO_BACKUP":     g("stash", "backup", "true").strip().lower() != "false",
        "API_BASE":      g("model", "api_base", "http://localhost:5001/v1").rstrip("/"),
        "API_KEY":       g("model", "api_key", "").strip(),
        "MODEL":         g("model", "model", "local").strip(),
        "TARGET_LANG":   g("translate", "target_lang", "简体中文").strip(),
        "PAUSE":         float(g("translate", "pause_seconds", "1.0")),
    }


CFG = load_config()


def post_json(url, payload, headers=None, timeout=180):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with OPENER.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def gql(query, variables=None):
    headers = {}
    if CFG["STASH_API_KEY"]:
        headers["ApiKey"] = CFG["STASH_API_KEY"]
        headers["Authorization"] = "Bearer " + CFG["STASH_API_KEY"]
    return post_json(CFG["STASH_URL"] + "/graphql",
                     {"query": query, "variables": variables or {}}, headers)


def chat_translate(text):
    """翻译一段文本，失败自动重试一次"""
    system = (
        f"你是专业的影视简介翻译。把用户给出的文本翻译成{CFG['TARGET_LANG']}。"
        "原文可能是英文或日文。要求：自然流畅、符合中文表达习惯、保留全部细节；"
        "只输出译文本身，不要任何解释、引号或多余内容。"
    )
    body = {
        "model": CFG["MODEL"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": text},
        ],
        "temperature": 0.3,
        "top_p": 0.9,
        "rep_pen": 1.05,
    }
    headers = {}
    if CFG["API_KEY"]:
        headers["Authorization"] = "Bearer " + CFG["API_KEY"]
    last_err = None
    for _ in range(2):
        try:
            resp = post_json(CFG["API_BASE"] + "/chat/completions", body, headers)
            return resp["choices"][0]["message"]["content"].strip()
        except Exception as e:
            last_err = e
            time.sleep(2)
    raise last_err


def is_translated(details):
    return "【中文简介】" in details


def mostly_chinese(text):
    # 含日文假名（ひらがな/カタカナ）的是日文，一律视为外语需要翻译，
    # 避免汉字占比高的日文被误判成"已是中文"而漏翻
    if any("぀" <= ch <= "ヿ" for ch in text):
        return False
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    letters = sum(1 for ch in text if ch.isalnum())
    return letters > 0 and cjk / letters > 0.4


def get_oshash(s):
    """取出场景第一个文件的 oshash 值（没有则返回空字符串）"""
    if not s.get("files"):
        return ""
    for fp in s["files"][0].get("fingerprints") or []:
        if fp.get("type") == "oshash" and fp.get("value"):
            return fp["value"]
    return ""


def find_by_oshash(scenes, oshash):
    target = oshash.strip().lower()
    for s in scenes:
        if get_oshash(s).lower() == target:
            return s
    return None


def confirm(prompt):
    """所有写操作前的安全锁：只有明确输入 Y 才继续"""
    try:
        ans = input(f"{prompt}（输入 Y 确认，直接回车或其他任意内容取消）: ").strip().upper()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return ans == "Y"


def backup_db():
    if not Path(CFG["DB_PATH"]).exists():
        print(f"错误：找不到 Stash 数据库文件 {CFG['DB_PATH']}")
        print("请用记事本打开 config.ini，把 [stash] 下的 db_path 改成你的实际路径。")
        sys.exit(1)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = Path(CFG["DB_PATH"]).with_name(f"stash.backup-{stamp}.sqlite")
    print(f"先备份数据库 -> {dest}")
    src = sqlite3.connect(Path(CFG["DB_PATH"]).as_uri() + "?mode=ro", uri=True)
    dst = sqlite3.connect(dest)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()


def fetch_scenes():
    scenes, page = [], 1
    while True:
        data = gql(
            "query($p:Int){ findScenes(filter:{page:$p, per_page:100})"
            "{ count scenes{ id details title files{ path fingerprints{ type value } } } } }",
            {"p": page},
        )["data"]["findScenes"]
        scenes += data["scenes"]
        if not data["scenes"] or len(scenes) >= data["count"]:
            break
        page += 1
    # 只保留有简介的
    return [s for s in scenes if s.get("details") and s["details"].strip()]


def update_details(scene_id, details):
    gql('mutation($i:ID!,$d:String){ sceneUpdate(input:{id:$i, details:$d}){ id } }',
        {"i": scene_id, "d": details})


def repair_empty_translations(scenes):
    """安全网：发现【中文简介】后是空的（模型曾返回空译文），就拆掉标记恢复原文，
    让下次运行自动重翻"""
    fixed = 0
    for s in scenes:
        d = s["details"]
        if "【中文简介】" not in d:
            continue
        orig, zh = d.split("【中文简介】", 1)
        if not zh.strip():
            update_details(s["id"], orig.rstrip())
            s["details"] = orig.rstrip()
            fixed += 1
    return fixed


def load_state():
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text("utf-8")))
    return set()


def save_state(state):
    STATE_FILE.write_text(json.dumps(sorted(state)), "utf-8")


def load_originals():
    if ORIGINALS_FILE.exists():
        return json.loads(ORIGINALS_FILE.read_text("utf-8"))
    return {}


def save_originals(originals):
    ORIGINALS_FILE.write_text(json.dumps(originals, ensure_ascii=False, indent=1), "utf-8")


def clear_one(s, state, originals):
    """只清除这一部：拆掉标记恢复原版，并从翻译记录里移除"""
    orig = s["details"].split("【中文简介】", 1)[0].rstrip()
    update_details(s["id"], orig)
    for k in (s["id"], str(s["id"])):
        state.discard(k)
        originals.pop(k, None)
    save_state(state)
    save_originals(originals)
    print("完成！这部已恢复原版简介（其他影片不受影响）。")
    print("以后想重翻这部，运行：")
    print(f"  python translate_details.py --oshash {get_oshash(s) or s['id']}")


def translate_one(s, state, originals):
    """只重翻这一部：清掉旧译文（如有）-> 重新翻译 -> 写回"""
    text = s["details"]
    if is_translated(text):
        text = text.split("【中文简介】", 1)[0].rstrip()
    print("正在请求模型翻译，请稍候（约 1~2 分钟）...", flush=True)
    try:
        zh = chat_translate(text)
    except Exception as e:
        print(f"失败：{e}")
        print("未做任何修改。请确认 KoboldCpp 已启动并加载了模型后重试。")
        return
    if not zh:
        print("失败：模型返回了空译文，未做任何修改。")
        return
    originals[s["id"]] = text
    update_details(s["id"], text + SEPARATOR + zh)
    state.add(s["id"])
    save_state(state)
    save_originals(originals)
    print("完成！新译文已写回 Stash。")
    print("不满意可加 --clear 恢复原版，或再次运行本命令换一次翻译结果。")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = set(sys.argv[1:])

    # 连接测试
    try:
        version = gql("{ version { version } }")["data"]["version"]["version"]
        print(f"已连接 Stash（版本 {version}）")
    except Exception as e:
        print(f"无法连接 Stash（{CFG['STASH_URL']}）：{e}")
        print("请先确认 Stash 正在运行，再运行本工具。")
        return

    scenes = fetch_scenes()
    repaired = repair_empty_translations(scenes)
    if repaired:
        print(f"自动修复了 {repaired} 条空译文（已恢复原文，本次会重新翻译它们）")

    # ---------- 单片操作模式（--oshash） ----------
    oshash = None
    if "--oshash" in sys.argv:
        i = sys.argv.index("--oshash")
        if i + 1 >= len(sys.argv):
            print("用法：python translate_details.py --oshash <oshash值> [--clear 或 --check]")
            return
        oshash = sys.argv[i + 1]

    if oshash:
        s = find_by_oshash(scenes, oshash)
        if not s:
            print(f"库内没有找到 oshash 为 {oshash} 的场景，请核对值是否正确。")
            return
        label = get_oshash(s) or f"(编号{s['id']})"
        state, originals = load_state(), load_originals()
        d = s["details"]

        # --check：只读不动，无需确认
        if "--check" in args:
            print(f"影片 oshash: {label}")
            if is_translated(d):
                orig, zh = d.split("【中文简介】", 1)
                print("状态：已翻译")
                print(f"原文预览：{orig.strip()[:80]}")
                print(f"译文预览：{zh.strip()[:80]}")
            else:
                print("状态：未翻译")
                print(f"原文预览：{d.strip()[:80]}")
            return

        # 写操作前先显示现状
        print(f"影片 oshash: {label}")
        if is_translated(d):
            orig, zh = d.split("【中文简介】", 1)
            print("当前状态：已翻译")
            print(f"原文预览：{orig.strip()[:60]}")
            print(f"译文预览：{zh.strip()[:60]}")
        else:
            print("当前状态：未翻译")
            print(f"原文预览：{d.strip()[:60]}")

        # --clear：只清除这一部
        if "--clear" in args:
            if not is_translated(d):
                print("这部片子的简介本来就没翻译，无需清除。")
                return
            if not confirm("确定清除这一部的翻译、恢复原版吗？（只影响这一部）"):
                print("已取消，未做任何修改。")
                return
            clear_one(s, state, originals)
            return

        # 默认：单独重翻这一部
        if not CFG["API_KEY"] and "localhost" not in CFG["API_BASE"] and "127.0.0.1" not in CFG["API_BASE"]:
            print("请先用记事本打开 config.ini，在 [model] 下填入 API_KEY，再运行。")
            return
        if not confirm("确定重翻这一部吗？（清除旧译文并重新翻译，只影响这一部）"):
            print("已取消，未做任何修改。")
            return
        translate_one(s, state, originals)
        return

    # ---------- 批量模式 ----------
    todo = [s for s in scenes
            if s.get("details") and not is_translated(s["details"]) and not mostly_chinese(s["details"])]
    print(f"库内共 {len(scenes)} 条带简介的场景，其中 {len(todo)} 条待翻译"
          f"（已翻译过或本就是中文的会自动跳过）")

    if "--check" in args:
        return

    if "--restore" in args:
        originals = load_originals()
        if not originals:
            print("没有找到任何翻译记录，无需还原。")
            return
        if not confirm(f"将把 {len(originals)} 条简介还原为原版，并清空全部翻译记录，确定吗？"):
            print("已取消，未做任何修改。")
            return
        ok = 0
        for sid, old in originals.items():
            try:
                update_details(sid, old)
                ok += 1
            except Exception as e:
                print(f"还原 {sid} 失败：{e}")
        save_originals({})
        STATE_FILE.unlink(missing_ok=True)
        print(f"已还原 {ok} 条简介为原版。")
        return

    if not CFG["API_KEY"] and "localhost" not in CFG["API_BASE"] and "127.0.0.1" not in CFG["API_BASE"]:
        print("请先用记事本打开 config.ini，在 [model] 下填入 API_KEY，再运行。")
        return

    limit = None
    if "--limit" in args:
        i = sys.argv.index("--limit")
        limit = int(sys.argv[i + 1])

    n = len(todo) if limit is None else min(limit, len(todo))
    if n == 0:
        print("没有需要翻译的简介，无事可做。")
        return
    if not confirm(f"即将先备份数据库，然后翻译 {n} 条简介，确定开始吗？"):
        print("已取消，未做任何修改。")
        return

    if CFG["DO_BACKUP"]:
        backup_db()
    state, originals = load_state(), load_originals()
    done = fail = 0
    for s in todo:
        if limit is not None and done >= limit:
            break
        sid, text = s["id"], s["details"]
        if sid in state:
            continue
        label = get_oshash(s) or f"(无oshash，编号{sid})"
        print(f"[{done + fail + 1}] 翻译 {label} ...", end=" ")
        try:
            zh = chat_translate(text)
            if not zh:
                raise RuntimeError("模型返回了空译文，按失败处理")
            originals[sid] = text
            update_details(sid, text + SEPARATOR + zh)
            state.add(sid)
            save_state(state)
            save_originals(originals)
            done += 1
            print("完成")
        except Exception as e:
            fail += 1
            print(f"失败：{e}")
        time.sleep(CFG["PAUSE"])

    print(f"\n本次完成 {done} 条，失败 {fail} 条。")
    if limit is not None and done >= limit:
        print("这是试运行结果。满意的话，运行 python translate_details.py 即可翻译全库。")
    if fail:
        print("失败的场景未做任何修改，重新运行脚本会自动重试。")
    print("如需撤销全部翻译、恢复原版，运行：python translate_details.py --restore")


if __name__ == "__main__":
    main()