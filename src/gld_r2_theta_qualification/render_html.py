"""Static owner and engineering projections for the sealed R2 Wave 1 manifest."""

from __future__ import annotations

from html import escape
from pathlib import PurePosixPath

from .contracts import R2QualificationError, canonical_json_bytes
from .publish import R2SyntheticAcceptanceManifestV1


_SCENARIO_TITLES_ZH = {
    "contract_identity": "合同与身份链",
    "registry_16_to_12": "16 个候选与 12 个可晋升候选",
    "entry_label_windows": "时间窗口与共同数据 mask",
    "policy_q1_stress": "政策、q=1 与压力路径",
    "joint_statistics": "Carrier 与联合统计",
    "forward_exact_100": "Exact-100 Forward 状态机",
}


def _document(
    manifest: R2SyntheticAcceptanceManifestV1,
) -> dict[str, object]:
    if type(manifest) is not R2SyntheticAcceptanceManifestV1:
        raise R2QualificationError("R2_ACCEPTANCE_MANIFEST_REQUIRED")
    return manifest.document


def _value(value: object) -> str:
    if value is None:
        return "—"
    if type(value) is bool:
        return "true" if value else "false"
    return escape(str(value), quote=True)


def _safe_href(value: object) -> str:
    if type(value) is not str or not value or "\\" in value or ":" in value:
        raise R2QualificationError("R2_ACCEPTANCE_PATH_INVALID")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise R2QualificationError("R2_ACCEPTANCE_PATH_INVALID")
    return escape(value, quote=True)


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    :root {{ color-scheme: light; font-family: -apple-system, BlinkMacSystemFont,
      "Segoe UI", "PingFang SC", sans-serif; color: #17202a; background: #f5f7fa; }}
    body {{ margin: 0; padding: 24px; }}
    main {{ max-width: 1180px; margin: 0 auto; }}
    h1 {{ margin: 0 0 8px; }} h2 {{ margin-top: 28px; }}
    p {{ line-height: 1.65; }}
    .subtitle {{ margin: 0 0 20px; color: #52616b; font-size: 17px; }}
    .boundary {{ padding: 14px 16px; border: 1px solid #d35400;
      border-radius: 10px; background: #fff4e6; font-weight: 650; }}
    .flow {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 10px; margin: 20px 0; }}
    .metric {{ padding: 14px; border: 1px solid #d9e1e8; border-radius: 10px;
      background: white; }}
    .metric span {{ display: block; color: #52616b; font-size: 13px; }}
    .metric strong {{ display: block; margin-top: 6px; overflow-wrap: anywhere; }}
    .owner-grid {{ display: grid; gap: 16px; grid-template-columns:
      repeat(auto-fit, minmax(280px, 1fr)); }}
    .owner-card {{ padding: 18px; border: 1px solid #cbd5df; border-radius: 14px;
      background: white; box-shadow: 0 2px 8px rgba(22, 32, 42, 0.05); }}
    .owner-card h2 {{ margin: 0 0 10px; font-size: 20px; }}
    .owner-card p {{ margin: 0; }}
    .next {{ margin-top: 22px; padding: 18px; border: 1px solid #9eb8ce;
      border-radius: 12px; background: #edf6ff; }}
    table {{ width: 100%; border-collapse: collapse; background: white; }}
    th, td {{ padding: 10px; border: 1px solid #d9e1e8; text-align: left;
      vertical-align: top; overflow-wrap: anywhere; }}
    th {{ background: #edf2f7; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 12px; }}
    a {{ color: #0b62a4; }}
    .pass {{ color: #155724; font-weight: 700; }}
    .pending {{ color: #8a4b08; font-weight: 700; }}
  </style>
</head>
<body><main>{body}</main></body>
</html>
"""


def render_owner_acceptance_index(
    manifest: R2SyntheticAcceptanceManifestV1,
) -> str:
    """Render the plain-language owner layer without hashes or controls."""

    document = _document(manifest)
    flow = document["candidate_flow"]
    assert type(flow) is dict
    body = f"""
<h1>Trading Platform｜R2 基础设施验收</h1>
<p class="subtitle">先确认筛选机器是否按批准的方法工作；这里没有真实策略结论。</p>
<p class="boundary">当前只完成本地 synthetic 基础设施：RESEARCH_ONLY / NO_DECISION_EFFECT / actionable=false / broker_order_count=0。</p>
<section class="flow" aria-label="R2 候选流程">
  <div class="metric"><span>规则目录</span><strong>{_value(flow['registry_count'])} 个候选</strong></div>
  <div class="metric"><span>允许晋升</span><strong>{_value(flow['promotable_count'])} 个可晋升</strong></div>
  <div class="metric"><span>安全对照</span><strong>{_value(flow['control_count'])} 个对照组</strong></div>
  <div class="metric"><span>历史评估集合</span><strong>K：尚未运行真实数据</strong></div>
  <div class="metric"><span>历史候选</span><strong>历史候选：0</strong></div>
  <div class="metric"><span>Forward</span><strong>Forward：0</strong></div>
  <div class="metric"><span>最终输出</span><strong>最终研究 θ：0</strong></div>
</section>
<section class="owner-grid">
  <article class="owner-card"><h2>现在可以验收</h2><p>候选目录、ENTRY/LABEL20、q=1、压力成本、联合统计和 Exact-100 状态机，都能用固定 synthetic fixture 重复运行并得到相同字节。</p></article>
  <article class="owner-card"><h2>一个候选是什么</h2><p>每个候选是一套 Gate × Hard Stop 的完整政策包；LC0 与 BCS0 是包内证据路径，不另外扩大搜索维度。</p></article>
  <article class="owner-card"><h2>未知不等于失败</h2><p>证据不足会停止当前 generation；只有充分证据下全部失败，才能形成 NO_QUALIFIED_POLICY。</p></article>
  <article class="owner-card"><h2>最多一个进入下一阶段</h2><p>未来只有一个精确政策 hash 可以等待 Owner 判断；当前没有 historical theta，也没有 qualification。</p></article>
  <article class="owner-card"><h2>Forward 还没有开始</h2><p>页面中的 Exact-100 只是本地状态机演练，不是 Forward 证据，也不证明策略有效。</p></article>
  <article class="owner-card"><h2>仍需真实数据和单独授权</h2><p>下一步必须先获得 metadata 授权；价格、收益、费用来源、历史 outcome 和 Forward reveal 都不在本次授权内。</p></article>
</section>
<section class="next">
  <h2>当前状态</h2>
  <p class="pass">R2_INFRASTRUCTURE_VERIFIED</p>
  <p class="pending">下一道门：METADATA_AUTHORIZATION_REQUIRED</p>
  <p><a href="engineering-evidence.html">查看工程证据</a></p>
</section>"""
    return _page("Trading Platform｜R2 基础设施验收", body)


def _canonical_text(value: object) -> str:
    return escape(canonical_json_bytes(value).decode("ascii"), quote=True)


def render_engineering_evidence(
    manifest: R2SyntheticAcceptanceManifestV1,
) -> str:
    """Render hashes, reason codes, paths, and explicit test boundaries."""

    document = _document(manifest)
    scenarios = document["scenarios"]
    r1_receipt = document["r1_regression_receipt"]
    determinism = document["determinism_receipt"]
    assert type(scenarios) is list and type(r1_receipt) is dict
    assert type(determinism) is dict
    rows = "".join(
        f"""<tr>
  <td><code>{_value(row['scenario_id'])}</code><br>{escape(_SCENARIO_TITLES_ZH[str(row['scenario_id'])])}</td>
  <td class="pass">{_value(row['automated_evidence_status'])}</td>
  <td><code>{_value(row['reason_code'])}</code></td>
  <td><code>{_canonical_text(row['expected'])}</code></td>
  <td><code>{_canonical_text(row['actual'])}</code></td>
  <td>{_value(row['test_boundary_zh'])}</td>
  <td><a href="{_safe_href(row['input_path'])}">{_value(row['input_path'])}</a><br><code>{_value(row['input_sha256'])}</code></td>
  <td><a href="{_safe_href(row['result_path'])}">{_value(row['result_path'])}</a><br><code>{_value(row['result_sha256'])}</code></td>
</tr>"""
        for row in scenarios
        if type(row) is dict
    )
    assets = r1_receipt["assets"]
    assert type(assets) is list
    asset_rows = "".join(
        f"""<tr><td>{_value(asset['path'])}</td><td class="pass">{_value(asset['status'])}</td>
<td><code>{_value(asset['actual_sha256'])}</code></td></tr>"""
        for asset in assets
        if type(asset) is dict
    )
    body = f"""
<h1>R2 工程证据</h1>
<p class="subtitle">Wave 1 synthetic-only 可复现证据；Owner 页面不展示这些工程细节。</p>
<p class="boundary">classification=SYNTHETIC_ONLY / scope=RESEARCH_ONLY / authority_status=NO_DECISION_EFFECT / actionable=false / broker_order_count=0</p>
<section class="flow">
  <div class="metric"><span>基础设施状态</span><strong>{_value(document['status'])}</strong></div>
  <div class="metric"><span>真实数据</span><strong>未访问</strong></div>
  <div class="metric"><span>真实科学终局</span><strong>真实科学终局：未生成</strong></div>
  <div class="metric"><span>Qualification</span><strong>未生成</strong></div>
  <div class="metric"><span>Owner receipt</span><strong>Owner receipt：未生成</strong></div>
</section>
<h2>Scenario evidence</h2>
<table><thead><tr><th>Scenario</th><th>Evidence</th><th>Reason</th><th>Expected</th><th>Actual</th><th>Test boundary</th><th>Input</th><th>Result</th></tr></thead><tbody>{rows}</tbody></table>
<h2>R1 byte regression</h2>
<p>Status: <strong class="pass">{_value(r1_receipt['status'])}</strong>；accepted commit: <code>{_value(r1_receipt['accepted_commit'])}</code></p>
<table><thead><tr><th>Asset</th><th>Status</th><th>SHA-256</th></tr></thead><tbody>{asset_rows}</tbody></table>
<h2>Determinism receipt</h2>
<p><strong class="pass">{_value(determinism['status'])}</strong></p>
<p>first=<code>{_value(determinism['first_run_sha256'])}</code><br>second=<code>{_value(determinism['second_run_sha256'])}</code></p>
<h2>Manifest</h2>
<p>Manifest 自身的 seal 与 rendered file hashes 保存在 canonical JSON 中，避免页面 hash 形成自引用。</p>
<p><a href="acceptance-manifest.json">查看 canonical acceptance manifest</a> · <a href="index.html">返回 Owner 页面</a></p>"""
    return _page("R2 工程证据", body)


__all__ = ["render_engineering_evidence", "render_owner_acceptance_index"]
