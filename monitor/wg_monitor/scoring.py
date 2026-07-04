"""内容评分器：把“是不是机会”从布尔命中升级为 0-100 加权分。

评分维度：
- 关键词命中：标题命中权重高于正文，封顶防刷分
- 行动信号：报名/招募/申请等动词
- 结构信号：独立报名链接、截止日期、明确活动时间
- 课表信号：有空加分、冲突减分
- 负面信号：公示/名单/总结/回顾等“事后文”强减分

三档判定（阈值可在 app.yml scoring 段调整）：
    score >= notify_min  → notify（发确认邮件/卡片提醒）
    score >= inbox_min   → inbox（只进收件箱，不打扰）
    否则                 → ignore

shadow_mode=true 时只记录分数与新旧判定分歧，不改变现有行为；
观察一段时间确认无误后再切 false 启用三档拦截。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .opportunity import OpportunityAnalysis


DEFAULT_NEGATIVE_KEYWORDS = [
    "公示",
    "名单",
    "总结",
    "回顾",
    "获奖",
    "结果公布",
    "表彰",
    "圆满",
    "风采",
    "成功举办",
    "落幕",
]

ACTION_KEYWORDS = ["报名", "招募", "申请", "征集", "招聘", "选拔", "纳新"]

DEFAULT_NOTIFY_MIN = 60
DEFAULT_INBOX_MIN = 40

# 各维度权重（v1 固定在代码里，阈值与负面词表可配）
TITLE_HIT_WEIGHT = 15
BODY_HIT_WEIGHT = 8
KEYWORD_CAP = 40
ACTION_FIRST_WEIGHT = 10
ACTION_EXTRA_WEIGHT = 4
ACTION_CAP = 20
SIGNUP_URL_BONUS = 10
DEADLINE_BONUS = 8
ACTIVITY_TIME_BONUS = 7
SCHEDULE_AVAILABLE_BONUS = 5
SCHEDULE_CONFLICT_PENALTY = -10
NEGATIVE_TITLE_WEIGHT = -18
NEGATIVE_BODY_WEIGHT = -10
NEGATIVE_CAP = -40


@dataclass(frozen=True)
class ScoreResult:
    score: int
    verdict: str  # notify | inbox | ignore
    reasons: list[str]

    def reasons_text(self) -> str:
        return "；".join(self.reasons)


def scoring_config(app_config: dict[str, Any]) -> dict[str, Any]:
    config = dict(app_config.get("scoring") or {})
    config.setdefault("enabled", True)
    config.setdefault("shadow_mode", True)
    config.setdefault("notify_min", DEFAULT_NOTIFY_MIN)
    config.setdefault("inbox_min", DEFAULT_INBOX_MIN)
    config.setdefault("negative_keywords", list(DEFAULT_NEGATIVE_KEYWORDS))
    return config


def score_opportunity(
    title: str,
    text: str,
    analysis: OpportunityAnalysis,
    app_config: dict[str, Any],
) -> ScoreResult:
    config = scoring_config(app_config)
    title = str(title or "")
    body = str(text or "")
    reasons: list[str] = []
    score = 0

    # 关键词命中（标题 > 正文）
    keyword_points = 0
    title_hits = [word for word in analysis.keyword_hits if word in title]
    body_hits = [word for word in analysis.keyword_hits if word not in title_hits]
    keyword_points += TITLE_HIT_WEIGHT * len(title_hits)
    keyword_points += BODY_HIT_WEIGHT * len(body_hits)
    keyword_points = min(keyword_points, KEYWORD_CAP)
    if keyword_points:
        score += keyword_points
        detail = "、".join(title_hits + body_hits)
        reasons.append(f"关键词命中 +{keyword_points}（{detail}）")

    # 行动信号
    combined = f"{title}\n{body}"
    action_hits = [word for word in ACTION_KEYWORDS if word in combined]
    if action_hits:
        action_points = min(
            ACTION_FIRST_WEIGHT + ACTION_EXTRA_WEIGHT * (len(action_hits) - 1), ACTION_CAP
        )
        score += action_points
        reasons.append(f"行动动词 +{action_points}（{'、'.join(action_hits)}）")

    # 结构信号
    if analysis.signup_url and analysis.signup_url != analysis.article_url:
        score += SIGNUP_URL_BONUS
        reasons.append(f"独立报名链接 +{SIGNUP_URL_BONUS}")
    if analysis.deadline:
        score += DEADLINE_BONUS
        reasons.append(f"有截止日期 +{DEADLINE_BONUS}")
    if analysis.activity_time:
        score += ACTIVITY_TIME_BONUS
        reasons.append(f"有活动时间 +{ACTIVITY_TIME_BONUS}")

    # 课表信号
    if analysis.schedule_status == "available":
        score += SCHEDULE_AVAILABLE_BONUS
        reasons.append(f"课表有空 +{SCHEDULE_AVAILABLE_BONUS}")
    elif analysis.schedule_status == "conflict":
        score += SCHEDULE_CONFLICT_PENALTY
        reasons.append(f"课表冲突 {SCHEDULE_CONFLICT_PENALTY}")

    # 负面信号（事后文/公示文）
    negative_points = 0
    negative_hits: list[str] = []
    for word in config["negative_keywords"]:
        word = str(word)
        if not word:
            continue
        if word in title:
            negative_points += NEGATIVE_TITLE_WEIGHT
            negative_hits.append(f"{word}(标题)")
        elif word in body:
            negative_points += NEGATIVE_BODY_WEIGHT
            negative_hits.append(word)
    negative_points = max(negative_points, NEGATIVE_CAP)
    if negative_points:
        score += negative_points
        reasons.append(f"疑似事后文 {negative_points}（{'、'.join(negative_hits)}）")

    score = max(0, min(100, score))
    # 阈值防呆：clamp 到 0-100，且 inbox_min 不得高于 notify_min（否则 inbox 档不可达）
    notify_min = max(0, min(100, int(config["notify_min"])))
    inbox_min = max(0, min(100, int(config["inbox_min"])))
    if inbox_min > notify_min:
        inbox_min = notify_min
    if score >= notify_min:
        verdict = "notify"
    elif score >= inbox_min:
        verdict = "inbox"
    else:
        verdict = "ignore"
    reasons.append(f"总分 {score}（notify≥{notify_min}，inbox≥{inbox_min}）")
    return ScoreResult(score=score, verdict=verdict, reasons=reasons)
