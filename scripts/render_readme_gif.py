#!/usr/bin/env python3
"""Render the README's taskplane flow infographic GIF.

The flow topology comes from each shipped ``skills/*/flow.json`` contract.
The short problem/outcome statements are the user-facing explanation layered
over those contracts.  This is a maintainer tool, not part of the runtime;
it requires Pillow and writes only the requested GIF/preview frames.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "docs" / "assets" / "taskplane-cowork-flow.gif"
SIZE = (1160, 808)

# Exact standalone dashboard palette from taskplane/dashboard.py._DOC_VARS.
PALETTE = {
    "surface0": "#f0efea",
    "surface1": "#f7f6f3",
    "surface2": "#ffffff",
    "border": "#dcd9d2",
    "line": "#b6b2aa",
    "accent": "#1f1e1c",
    "text": "#1f1e1c",
    "secondary": "#55524c",
    "muted": "#8b877f",
    "danger": "#a8331f",
    "danger_bg": "#f6e3df",
    "warning": "#8a5a10",
    "warning_bg": "#f8ecd6",
    "changed": "#eceae4",
}


STORIES = [
    {
        "skill": "taskplane",
        "title": "One goal. The right governed flow.",
        "problem": "Users should not have to learn personas, graph depth, lens routing, or loop commands.",
        "outcome": "A simple design, build, review, or status request routes to the right strict workflow and surfaces only real decisions.",
        "prompt": "taskplane build safe order cancellation",
    },
    {
        "skill": "tp-product",
        "title": "Define the WHAT before agents build.",
        "problem": "Ambiguous ideas become code before acceptance, dependencies, and product risks are settled.",
        "outcome": "A complete requirement, Product DoR, review, and explicit sign-off trigger governed Build only when the work is ready.",
        "prompt": "taskplane spec this feature",
    },
    {
        "skill": "tp-design",
        "title": "Approve the HOW before code changes.",
        "problem": "Architecture and contract choices are otherwise made implicitly during implementation.",
        "outcome": "Alternatives, graph overlay, trade-offs, rollout, and validation are sealed in a human-approved Design Contract.",
        "prompt": "taskplane design safe cancellation",
    },
    {
        "skill": "tp-build",
        "title": "Build new features without trusting “done.”",
        "problem": "The first implementation wins by default, while readiness, alternatives, and downstream impact stay implicit.",
        "outcome": "Product, Design, Plan, A/B selection, Evaluate, Review, sign-off, and Retro remain one evidence-backed feature flow.",
        "prompt": "taskplane build this as A/B variants",
    },
    {
        "skill": "tp-go",
        "title": "Stop skipped steps and partial delivery.",
        "problem": "Agents optimize for a fast answer: they drift scope, skip graph work, and report incomplete execution as finished.",
        "outcome": "Scoped workers submit evidence; independent gates advance the loop; only humans approve plans and sign off outcomes.",
        "prompt": "start governed work",
    },
    {
        "skill": "tp-engineering",
        "title": "Review the blast radius, not just the diff.",
        "problem": "Broad reviews reread the same repository, waste tokens, and still miss dependencies outside changed files.",
        "outcome": "One diff and graph impact route only applicable lenses into leased views, one canonical revision, and a human decision.",
        "prompt": "taskplane review this PR",
    },
    {
        "skill": "tp-status",
        "title": "Know where work stands—and who acts next.",
        "problem": "Long agent runs hide the active stage, open gate, dependency risk, and next owner.",
        "outcome": "Mission control joins loop state, requirements, debt, and graph impact with one explicit action banner.",
        "prompt": "taskplane status",
    },
    {
        "skill": "tp-northstar",
        "title": "Keep locally good work aligned to direction.",
        "problem": "A technically sound idea can still consume time without serving the product's north star.",
        "outcome": "An advisory check makes leverage, reversibility, opportunity cost, coherence, and the sharpest tension explicit.",
        "prompt": "north-star this proposal",
    },
    {
        "skill": "tp-help",
        "title": "Get to one useful next action.",
        "problem": "Setup mechanics and a large skill catalog can delay the first governed result.",
        "outcome": "A short mental model plus folder, Git, init, and hook readiness routes the user to the correct next step.",
        "prompt": "taskplane help",
    },
    {
        "skill": "tp-tag",
        "title": "Turn team chat into attributable delivery.",
        "problem": "Slack decisions lose context, ownership, evidence, and durable state across handoffs.",
        "outcome": "The thread drives a repo-persisted loop with attached dashboards, attributed approval, and resumable team memory.",
        "prompt": "@Claude use taskplane for this",
    },
]


def _font_paths() -> tuple[Path | None, Path | None, Path | None]:
    choices = [
        (Path("/System/Library/Fonts/SFNS.ttf"),
         Path("/System/Library/Fonts/SFNS.ttf"),
         Path("/System/Library/Fonts/SFNSMono.ttf")),
        (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
         Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
         Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")),
    ]
    for regular, bold, mono in choices:
        if regular.is_file() and bold.is_file() and mono.is_file():
            return regular, bold, mono
    return None, None, None


REGULAR_PATH, BOLD_PATH, MONO_PATH = _font_paths()


def font(size: int, *, bold: bool = False, mono: bool = False):
    path = MONO_PATH if mono else (BOLD_PATH if bold else REGULAR_PATH)
    if path:
        return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default(size=size)


F = {
    "kicker": font(13, mono=True),
    "small": font(14),
    "small_bold": font(14, bold=True),
    "body": font(19),
    "body_bold": font(19, bold=True),
    "title": font(42, bold=True),
    "hero": font(52, bold=True),
    "node": font(13, bold=True),
    "node_meta": font(10, mono=True),
    "prompt": font(14, mono=True),
}


def wrap(draw: ImageDraw.ImageDraw, text: str, face, width: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        candidate = word if not current else current + " " + word
        if draw.textbbox((0, 0), candidate, font=face)[2] <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def paragraph(draw, xy, text, face, fill, width, spacing=7, max_lines=None):
    lines = wrap(draw, text, face, width)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines[-1] and draw.textbbox((0, 0), lines[-1] + "…", font=face)[2] > width:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "…"
    x, y = xy
    line_h = draw.textbbox((0, 0), "Ag", font=face)[3]
    for line in lines:
        draw.text((x, y), line, font=face, fill=fill)
        y += line_h + spacing
    return y


def header(draw, version: str, index: int, total: int):
    draw.text((68, 44), "TASKPLANE", font=F["kicker"], fill=PALETTE["secondary"])
    pill = f"v{version}  ·  CLAUDE + CODEX"
    box = draw.textbbox((0, 0), pill, font=F["kicker"])
    w = box[2] - box[0] + 26
    draw.rounded_rectangle((SIZE[0] - 68 - w, 34, SIZE[0] - 68, 67), radius=16,
                           fill=PALETTE["surface1"], outline=PALETTE["border"])
    draw.text((SIZE[0] - 68 - w + 13, 44), pill, font=F["kicker"], fill=PALETTE["secondary"])
    draw.text((68, 752), f"{index:02d} / {total:02d}", font=F["kicker"], fill=PALETTE["muted"])


def arrow(draw, start, end, color=None, width=2):
    color = color or PALETTE["line"]
    draw.line((start, end), fill=color, width=width)
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    length = 8
    for delta in (2.55, -2.55):
        point = (end[0] + length * math.cos(angle + delta),
                 end[1] + length * math.sin(angle + delta))
        draw.line((end, point), fill=color, width=width)


def node_style(kind: str):
    if kind == "gate":
        return PALETTE["danger_bg"], PALETTE["danger"], "HUMAN GATE"
    if kind == "parallel":
        return PALETTE["warning_bg"], PALETTE["warning"], "PARALLEL"
    if kind in {"control", "decision"}:
        return PALETTE["accent"], PALETTE["accent"], kind.upper()
    if kind == "visual":
        return PALETTE["changed"], PALETTE["accent"], "VISUAL"
    return PALETTE["surface2"], PALETTE["line"], kind.upper()


def draw_node(draw, rect, node):
    fill, stroke, meta = node_style(str(node.get("kind") or "stage"))
    draw.rounded_rectangle(rect, radius=7, fill=fill, outline=stroke, width=2 if node.get("kind") == "gate" else 1)
    dark = node.get("kind") in {"control", "decision"}
    text = PALETTE["surface2"] if dark else PALETTE["text"]
    muted = PALETTE["border"] if dark else PALETTE["muted"]
    x1, y1, x2, y2 = rect
    label = str(node.get("label") or node.get("id") or "")
    lines = wrap(draw, label, F["node"], int(x2 - x1 - 18))[:2]
    y = y1 + 7
    for line in lines:
        draw.text((x1 + 9, y), line, font=F["node"], fill=text)
        y += 15
    draw.text((x1 + 9, y2 - 15), meta, font=F["node_meta"], fill=muted)


def ranked_layout(nodes, edges, area):
    ids = [node["id"] for node in nodes]
    incoming = {node_id: [] for node_id in ids}
    for left, right in edges:
        if left in incoming and right in incoming:
            incoming[right].append(left)
    rank = {}
    pending = set(ids)
    while pending:
        progressed = False
        for node_id in list(pending):
            parents = incoming[node_id]
            if all(parent in rank for parent in parents):
                rank[node_id] = 0 if not parents else max(rank[parent] for parent in parents) + 1
                pending.remove(node_id)
                progressed = True
        if not progressed:
            return None
    max_rank = max(rank.values(), default=0)
    if max_rank > 5:
        return None
    x1, y1, x2, y2 = area
    by_rank = {value: [] for value in range(max_rank + 1)}
    for node in nodes:
        by_rank[rank[node["id"]]].append(node)
    node_w = min(128, int((x2 - x1) / max(1, max_rank + 1) - 16))
    positions = {}
    for value, group in by_rank.items():
        cx = x1 + (value + 0.5) * (x2 - x1) / (max_rank + 1)
        node_h = min(62, (y2 - y1 - 18 * (len(group) - 1)) / max(1, len(group)))
        total_h = len(group) * node_h + max(0, len(group) - 1) * 18
        top = y1 + (y2 - y1 - total_h) / 2
        for idx, node in enumerate(group):
            positions[node["id"]] = (cx - node_w / 2, top + idx * (node_h + 18),
                                      cx + node_w / 2, top + idx * (node_h + 18) + node_h)
    return positions


def vertical_layout(nodes, area):
    x1, y1, x2, y2 = area
    count = max(1, len(nodes))
    gap = 8 if count >= 10 else 12
    node_h = min(48, (y2 - y1 - gap * (count - 1)) / count)
    total_h = count * node_h + gap * (count - 1)
    top = y1 + (y2 - y1 - total_h) / 2
    width = x2 - x1 - 94
    left = x1 + 47
    return {node["id"]: (left, top + i * (node_h + gap), left + width,
                          top + i * (node_h + gap) + node_h)
            for i, node in enumerate(nodes)}


def draw_flow(draw, flow, area):
    nodes, edges = flow["nodes"], flow["edges"]
    positions = ranked_layout(nodes, edges, area) or vertical_layout(nodes, area)
    for left, right in edges:
        if left not in positions or right not in positions:
            continue
        a, b = positions[left], positions[right]
        if abs((b[0] + b[2]) - (a[0] + a[2])) > 80:
            start, end = (a[2], (a[1] + a[3]) / 2), (b[0], (b[1] + b[3]) / 2)
        else:
            start, end = ((a[0] + a[2]) / 2, a[3]), ((b[0] + b[2]) / 2, b[1])
        arrow(draw, start, end)
    for node in nodes:
        draw_node(draw, positions[node["id"]], node)


def base_frame(version: str, index: int, total: int):
    image = Image.new("RGB", SIZE, PALETTE["surface2"])
    draw = ImageDraw.Draw(image)
    header(draw, version, index, total)
    return image, draw


def overview_frame(version: str, index: int, total: int):
    image, draw = base_frame(version, index, total)
    draw.text((68, 126), "Simple for you.", font=F["hero"], fill=PALETTE["text"])
    draw.text((68, 184), "Strict for agents.", font=F["hero"], fill=PALETTE["text"])
    paragraph(draw, (70, 263), "Four outcome-shaped prompts enter one control plane. The graph, contracts, evidence, selective review, and human gates stay behind the interface.", F["body"], PALETTE["secondary"], 780)
    prompts = ["DESIGN", "BUILD", "REVIEW", "STATUS"]
    x = 70
    centers = []
    for prompt in prompts:
        rect = (x, 395, x + 150, 443)
        draw.rounded_rectangle(rect, radius=8, fill=PALETTE["surface1"], outline=PALETTE["border"])
        draw.text((x + 18, 411), prompt, font=F["kicker"], fill=PALETTE["secondary"])
        centers.append((x + 75, 443))
        x += 170
    hub = (330, 520, 620, 592)
    for center in centers:
        arrow(draw, center, ((hub[0] + hub[2]) / 2, hub[1]), PALETTE["line"])
    draw.rounded_rectangle(hub, radius=9, fill=PALETTE["accent"], outline=PALETTE["accent"])
    draw.text((355, 539), "TASKPLANE CONTROL PLANE", font=F["body_bold"], fill=PALETTE["surface2"])
    outcomes = ["READY", "SCOPED", "PROVEN", "HUMAN-APPROVED"]
    x = 230
    for outcome in outcomes:
        rect = (x, 655, x + 175, 697)
        arrow(draw, ((hub[0] + hub[2]) / 2, hub[3]), (x + 87, rect[1]))
        draw.rounded_rectangle(rect, radius=18, fill=PALETTE["changed"], outline=PALETTE["line"])
        draw.text((x + 16, 669), outcome, font=F["kicker"], fill=PALETTE["secondary"])
        x += 190
    return image


def story_frame(version: str, story: dict, flow: dict, index: int, total: int):
    image, draw = base_frame(version, index, total)
    draw.text((68, 105), story["skill"].upper(), font=F["kicker"], fill=PALETTE["muted"])
    paragraph(draw, (68, 137), story["title"], F["title"], PALETTE["text"], 430, spacing=4, max_lines=3)

    draw.text((68, 285), "PROBLEM", font=F["kicker"], fill=PALETTE["danger"])
    problem_bottom = paragraph(draw, (68, 312), story["problem"], F["body"], PALETTE["secondary"], 400, max_lines=5)
    draw.text((68, problem_bottom + 22), "TASKPLANE OUTCOME", font=F["kicker"], fill=PALETTE["secondary"])
    outcome_bottom = paragraph(draw, (68, problem_bottom + 49), story["outcome"], F["body"], PALETTE["text"], 400, max_lines=6)

    prompt_y = max(650, outcome_bottom + 26)
    draw.rounded_rectangle((68, prompt_y, 455, prompt_y + 52), radius=8,
                           fill=PALETTE["surface1"], outline=PALETTE["border"])
    draw.text((83, prompt_y + 8), "TRY", font=F["node_meta"], fill=PALETTE["muted"])
    draw.text((83, prompt_y + 25), story["prompt"], font=F["prompt"], fill=PALETTE["text"])

    panel = (500, 105, 1092, 720)
    draw.rounded_rectangle(panel, radius=12, fill=PALETTE["surface1"], outline=PALETTE["border"])
    draw.text((524, 126), "FLOW CONTRACT", font=F["kicker"], fill=PALETTE["muted"])
    draw.text((524, 150), story["skill"], font=F["small_bold"], fill=PALETTE["text"])
    draw_flow(draw, flow, (518, 186, 1074, 698))
    return image


def benefit_frame(version: str, index: int, total: int):
    image, draw = base_frame(version, index, total)
    draw.text((68, 112), "The benefit is the whole system.", font=F["hero"], fill=PALETTE["text"])
    paragraph(draw, (70, 180), "Each flow solves a specific problem. Together they make agent delivery simpler to operate, harder to skip, cheaper to review, and easier to trust.", F["body"], PALETTE["secondary"], 850)
    benefits = [
        ("LESS USER COMPLEXITY", "Four prompts route ten flows; users see only progress, evidence, and decisions."),
        ("HIGHER CORRECTNESS", "Product DoR, graph-aware plans, DoD, review, and Retro keep intent connected to code."),
        ("LOWER REVIEW WASTE", "One diff and blast radius feed selective lenses through immutable scoped views."),
        ("SAFER EXECUTION", "Contracts constrain scope; workers submit; independent gates decide; humans approve."),
        ("COMPOUNDING CONTEXT", "Requirements, decisions, debt, graph truth, and lessons survive the next task."),
        ("HONEST STATUS", "Mission control names the active stage, evidence, blocker, and next owner."),
    ]
    for i, (title, body) in enumerate(benefits):
        col, row = i % 2, i // 2
        x, y = 68 + col * 522, 295 + row * 133
        draw.rounded_rectangle((x, y, x + 486, y + 108), radius=10,
                               fill=PALETTE["surface1"], outline=PALETTE["border"])
        draw.text((x + 18, y + 17), title, font=F["kicker"], fill=PALETTE["secondary"])
        paragraph(draw, (x + 18, y + 43), body, F["small"], PALETTE["text"], 445, spacing=4, max_lines=3)
    draw.rounded_rectangle((324, 704, 836, 744), radius=20,
                           fill=PALETTE["accent"], outline=PALETTE["accent"])
    draw.text((377, 716), "DESIGN → BUILD → REVIEW → LEARN", font=F["kicker"], fill=PALETTE["surface2"])
    return image


def load_flows() -> dict[str, dict]:
    flows = {}
    for path in sorted((ROOT / "skills").glob("*/flow.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        flows[str(value["skill"])] = value
    missing = sorted({story["skill"] for story in STORIES} - flows.keys())
    if missing:
        raise SystemExit("missing flow contracts: " + ", ".join(missing))
    return flows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--preview-dir", type=Path)
    args = parser.parse_args()

    manifest = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    version = str(manifest["version"])
    flows = load_flows()
    total = len(STORIES) + 2
    frames = [overview_frame(version, 1, total)]
    for idx, story in enumerate(STORIES, start=2):
        frames.append(story_frame(version, story, flows[story["skill"]], idx, total))
    frames.append(benefit_frame(version, total, total))

    if args.preview_dir:
        args.preview_dir.mkdir(parents=True, exist_ok=True)
        for idx, frame in enumerate(frames, start=1):
            frame.save(args.preview_dir / f"frame-{idx:02d}.png", optimize=True)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    quantized = [frame.quantize(colors=96, method=Image.Quantize.MEDIANCUT,
                                dither=Image.Dither.NONE) for frame in frames]
    durations = [3400] + [4100] * len(STORIES) + [5200]
    quantized[0].save(args.output, save_all=True, append_images=quantized[1:],
                      duration=durations, loop=0, optimize=True, disposal=2)
    print(f"rendered {len(frames)} frames · {SIZE[0]}x{SIZE[1]} · {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
