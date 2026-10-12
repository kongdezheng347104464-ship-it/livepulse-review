import os, sys, json, subprocess, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

# 从环境变量读取阿里云凭证(见 .env.example),不要硬编码
for _k in ("ALIYUN_ACCESS_KEY_ID","ALIYUN_ACCESS_KEY_SECRET","ALIYUN_ASR_APPKEY","ALIYUN_OSS_BUCKET"):
    if not os.environ.get(_k):
        sys.exit(f"缺少环境变量 {_k},请先配置 .env")


FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")

video = r"E:\WorkBuddy.1\2026-05-21-10-10-04\uploaded_video.mp4"
audio = r"E:\WorkBuddy.1\2026-05-21-10-10-04\audio_uploaded.wav"

print("=== Step 1: 提取音频 ===")
subprocess.run([FFMPEG, "-y", "-i", video, "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", audio], check=True)
size_mb = os.path.getsize(audio) / 1024 / 1024
print(f"音频提取完成: {size_mb:.0f}MB")

print("\n=== Step 2: 阿里云 ASR 转写 ===")
import importlib.util
spec = importlib.util.spec_from_file_location("asr_service", os.path.join(os.path.dirname(__file__), "backend", "app", "services", "asr_service.py"))
asr_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(asr_mod)
transcribe = asr_mod.transcribe
t0 = time.time()
segments = transcribe(audio)
elapsed = time.time() - t0
print(f"转写完成: {len(segments)} 段, 耗时 {elapsed:.0f}s")

print(f"\n=== Step 3: 话术分析 ===")
spec2 = importlib.util.spec_from_file_location("script_analyzer", os.path.join(os.path.dirname(__file__), "backend", "app", "services", "script_analyzer.py"))
sa_mod = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(sa_mod)
analyze_script_segments = sa_mod.analyze_script_segments
results = analyze_script_segments(segments)
print(f"分析完成: {len(results)} 段")

report_path = r"E:\WorkBuddy.1\2026-05-21-10-10-04\analysis_report.md"
print(f"\n=== Step 4: 生成报告 → {report_path} ===")

duration = segments[-1]["end"] - segments[0]["start"] if segments else 0
total_chars = sum(len(r["text"]) for r in results)
types = {}
avg_scores = {}
for r in results:
    t = r["script_type"]
    types[t] = types.get(t, 0) + 1
    if r["score"] is not None:
        avg_scores.setdefault(t, []).append(float(r["score"]))

lines = [
    "# 直播话术分析报告",
    "",
    f"**视频**: 2026-05-20 12-47-39.mp4",
    f"**时长**: {int(duration // 60)} 分 {int(duration % 60)} 秒",
    f"**话术段落**: {len(results)} 段",
    f"**总字数**: {total_chars}",
    f"**平均语速**: {round(total_chars / max(duration / 60, 1)):.0f} 字/分钟",
    "",
    "---",
    "",
    "## 话术分类分布",
    "",
    "| 类型 | 段数 | 占比 | 平均分 |",
    "|------|------|------|--------|",
]
type_labels = {"product_intro": "产品介绍", "promotion": "促销", "interaction": "互动", "chitchat": "闲聊"}
for t in ["product_intro", "promotion", "interaction", "chitchat"]:
    cnt = types.get(t, 0)
    pct = round(cnt / len(results) * 100, 1) if results else 0
    avg = round(sum(avg_scores.get(t, [0])) / max(len(avg_scores.get(t, [])), 1), 1)
    lines.append(f"| {type_labels[t]} | {cnt} | {pct}% | {avg} |")

lines.extend([
    "",
    "---",
    "",
    "## 话术时间线",
    "",
])

for i, r in enumerate(results[:80]):
    m0, s0 = int(r["start_sec"] // 60), int(r["start_sec"] % 60)
    m1, s1 = int(r["end_sec"] // 60), int(r["end_sec"] % 60)
    labels = type_labels.get(r["script_type"], r["script_type"])
    score_str = f" [{r['score']}分]" if r.get("score") else ""
    kw_str = ""
    if r.get("keywords"):
        kw_str = " 🔑 " + " · ".join(r["keywords"][:5])
    lines.append(f"- **[{m0}:{s0:02d} - {m1}:{s1:02d}]** `{labels}`{score_str} — {r['text'][:120]}{kw_str}")

if len(results) > 80:
    lines.append(f"\n... 还有 {len(results) - 80} 段，详见完整数据")

lines.extend([
    "",
    "---",
    "",
    "## 关键词 TOP 20",
    "",
])

from collections import Counter
kw_counter = Counter()
for r in results:
    for kw in r.get("keywords", []) or []:
        kw_counter[kw] += 1

for kw, cnt in kw_counter.most_common(20):
    lines.append(f"- **{kw}** ({cnt} 次)")

with open(report_path, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print("报告生成完毕！")
