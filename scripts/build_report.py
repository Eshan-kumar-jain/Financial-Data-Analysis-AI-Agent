"""Generate the PBIR report pages for powerbi/anomaly_detection.Report.

The report canvas is not reachable through the Power BI MCP, so the pages are
authored as PBIR JSON (Power BI Project format) by this script instead:
definition/pages/<page>/page.json and visuals/<visual>/visual.json for five
pages, plus pages.json. Re-runnable - it rewrites the pages folder, keeping any
Desktop-authored mobile layouts (mobile.json) for visuals that still exist.

Not managed here: the theme and report.json (set in Desktop), and the semantic
model (built through the MCP; measures validated by scripts/validate_dax.py).

Run from the repo root with Power BI Desktop CLOSED (it holds the files open
and would overwrite them on its next save):
  .venv/Scripts/python.exe scripts/build_report.py
Then open powerbi/anomaly_detection.pbip.
"""
import json
import shutil
from pathlib import Path

REPORT = Path(__file__).resolve().parents[1] / "powerbi" / "anomaly_detection.Report"
DEF = REPORT / "definition"
PAGES = DEF / "pages"

VC_SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.4.0/schema.json"
PAGE_SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.1.0/schema.json"
PAGES_SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pagesMetadata/1.1.0/schema.json"

W, H = 1280, 720
BLUE, ORANGE = "#2a78d6", "#eb6834"   # validated categorical palette, slots 1-2
INK, INK_2 = "#0b0b0b", "#52514e"


# --- expression helpers ------------------------------------------------------
def lit(v):
    """Power BI literal: strings are single-quoted inside the Value string."""
    if isinstance(v, bool):
        return {"expr": {"Literal": {"Value": "true" if v else "false"}}}
    if isinstance(v, (int, float)):
        return {"expr": {"Literal": {"Value": f"{v}D"}}}
    return {"expr": {"Literal": {"Value": f"'{v}'"}}}


def color(hex_):
    return {"solid": {"color": lit(hex_)}}


def col(table, name):
    return {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": name}}


def msr(table, name):
    return {"Measure": {"Expression": {"SourceRef": {"Entity": table}}, "Property": name}}


def agg(table, name, fn=0):
    # fn: 0 Sum, 1 Avg, 3 Min, 4 Max
    return {"Aggregation": {"Expression": col(table, name), "Function": fn}}


def ref(field):
    if "Measure" in field:
        e = field["Measure"]
        return f"{e['Expression']['SourceRef']['Entity']}.{e['Property']}"
    if "Column" in field:
        e = field["Column"]
        return f"{e['Expression']['SourceRef']['Entity']}.{e['Property']}"
    e = field["Aggregation"]["Expression"]["Column"]
    fn = {0: "Sum", 1: "Avg", 3: "Min", 4: "Max"}[field["Aggregation"]["Function"]]
    return f"{fn}({e['Expression']['SourceRef']['Entity']}.{e['Property']})"


def proj(field, display=None):
    p = {"field": field, "queryRef": ref(field), "nativeQueryRef": ref(field).split(".")[-1].rstrip(")")}
    if display:
        p["displayName"] = display
    return p


def in_filter(name, table, column, values):
    """Categorical 'column IN (values)' filter."""
    return {
        "name": name,
        "field": col(table, column),
        "type": "Categorical",
        "filter": {
            "Version": 2,
            "From": [{"Name": "t", "Entity": table, "Type": 0}],
            "Where": [{"Condition": {"In": {
                "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "t"}}, "Property": column}}],
                "Values": [[{"Literal": {"Value": f"'{v}'"}}] for v in values],
            }}}],
        },
        "howCreated": "User",
    }


# --- visual builders ---------------------------------------------------------
_counter = {"n": 0}


def vid(prefix):
    _counter["n"] += 1
    return f"{prefix}{_counter['n']:03d}"


def container(name, x, y, w, h, visual, filters=None, z=0):
    c = {
        "$schema": VC_SCHEMA,
        "name": name,
        "position": {"x": x, "y": y, "z": z, "height": h, "width": w, "tabOrder": z},
        "visual": visual,
    }
    if filters:
        c["filterConfig"] = {"filters": filters}
    return c


def title_objs(text, subtitle=None):
    o = {"title": [{"properties": {
        "show": lit(True), "text": lit(text), "fontSize": lit(12), "bold": lit(True), "fontColor": color(INK)}}]}
    if subtitle:
        o["subTitle"] = [{"properties": {"show": lit(True), "text": lit(subtitle), "fontColor": color(INK_2)}}]
    return o


def textbox(x, y, w, h, paragraphs):
    """paragraphs: list of (text, size_pt, bold, hex)."""
    paras = [{"textRuns": [{"value": t, "textStyle": {
        "fontSize": f"{s}pt", "fontWeight": "bold" if b else "normal", "color": c}}]}
        for t, s, b, c in paragraphs]
    return container(vid("txt"), x, y, w, h, {
        "visualType": "textbox",
        "objects": {"general": [{"properties": {"paragraphs": paras}}]},
    })


def card(x, y, w, h, field, label):
    return container(vid("card"), x, y, w, h, {
        "visualType": "card",
        "query": {"queryState": {"Values": {"projections": [proj(field, label)]}}},
        "objects": {
            "labels": [{"properties": {"color": color(INK), "fontSize": lit(26)}}],
            "categoryLabels": [{"properties": {"show": lit(True), "color": color(INK_2), "fontSize": lit(10)}}],
        },
        "drillFilterOtherVisuals": True,
    })


def bar(x, y, w, h, vtype, category, values, title, subtitle=None, colors=None,
        sort_field=None, sort_dir="Descending", filters=None, labels=True):
    visual = {
        "visualType": vtype,
        "query": {"queryState": {
            "Category": {"projections": [proj(category)]},
            "Y": {"projections": [proj(v, d) for v, d in values]},
        }},
        "objects": {
            "labels": [{"properties": {"show": lit(labels), "color": color(INK_2)}}],
            "legend": [{"properties": {"show": lit(len(values) > 1), "position": lit("Top")}}],
        },
        "visualContainerObjects": title_objs(title, subtitle),
        "drillFilterOtherVisuals": True,
    }
    if colors:
        visual["objects"]["dataPoint"] = [
            {"properties": {"fill": color(c)}, "selector": {"metadata": ref(v)}}
            for (v, _), c in zip(values, colors)
        ]
    if sort_field is not None:
        visual["query"]["sortDefinition"] = {
            "sort": [{"field": sort_field, "direction": sort_dir}], "isDefaultSort": False}
    return container(vid("bar"), x, y, w, h, visual, filters)


def line(x, y, w, h, category, values, title, subtitle=None, colors=None):
    visual = {
        "visualType": "lineChart",
        "query": {"queryState": {
            "Category": {"projections": [proj(category)]},
            "Y": {"projections": [proj(v, d) for v, d in values]},
        }, "sortDefinition": {"sort": [{"field": category, "direction": "Ascending"}], "isDefaultSort": False}},
        "objects": {
            "legend": [{"properties": {"show": lit(len(values) > 1), "position": lit("Top")}}],
            "lineStyles": [{"properties": {"strokeWidth": lit(2)}}],
        },
        "visualContainerObjects": title_objs(title, subtitle),
        "drillFilterOtherVisuals": True,
    }
    if colors:
        visual["objects"]["dataPoint"] = [
            {"properties": {"fill": color(c)}, "selector": {"metadata": ref(v)}}
            for (v, _), c in zip(values, colors)
        ]
    return container(vid("line"), x, y, w, h, visual)


def table(x, y, w, h, fields, title, subtitle=None, filters=None, sort_field=None):
    visual = {
        "visualType": "tableEx",
        "query": {"queryState": {"Values": {"projections": [proj(f, d) for f, d in fields]}}},
        # Totals off, not computed: at the total row every method is in context,
        # and adding flags / precision across methods is invalid by design (the
        # measures blank themselves there), so an honest total does not exist.
        "objects": {"total": [{"properties": {"totals": lit(False)}}]},
        "visualContainerObjects": title_objs(title, subtitle),
        "drillFilterOtherVisuals": True,
    }
    if sort_field is not None:
        visual["query"]["sortDefinition"] = {
            "sort": [{"field": sort_field, "direction": "Ascending"}], "isDefaultSort": False}
    return container(vid("tbl"), x, y, w, h, visual, filters)


def matrix(x, y, w, h, rows, columns, value, title, subtitle=None, filters=None, heat=None):
    visual = {
        "visualType": "pivotTable",
        "query": {"queryState": {
            "Rows": {"projections": [proj(rows)]},
            "Columns": {"projections": [proj(columns)]},
            "Values": {"projections": [proj(value)]},
        }, "sortDefinition": {"sort": [{"field": rows, "direction": "Ascending"}], "isDefaultSort": False}},
        "objects": {
            "subTotals": [{"properties": {"rowSubtotals": lit(False), "columnSubtotals": lit(False)}}],
        },
        "visualContainerObjects": title_objs(title, subtitle),
        "drillFilterOtherVisuals": True,
    }
    if heat:
        # background gradient white -> heat colour on the value, per cell
        visual["objects"]["values"] = [{
            "properties": {"backColor": {"solid": {"color": {"expr": {"FillRule": {
                "Input": value,
                "FillRule": {"linearGradient2": {
                    "min": {"color": {"Literal": {"Value": "'#FFFFFF'"}}},
                    "max": {"color": {"Literal": {"Value": f"'{heat}'"}}},
                    "nullColoringStrategy": {"strategy": {"Literal": {"Value": "'asZero'"}}},
                }},
            }}}}}},
            "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}], "metadata": ref(value)},
        }]
    return container(vid("mtx"), x, y, w, h, visual, filters)


# --- fields ------------------------------------------------------------------
F = lambda n: msr("eval_entry_flag", n)          # header-level + layer header measures
L = lambda n: msr("eval_entry_label", n)         # pair-level + layer pair measures
S = lambda n: msr("eval_threshold_sweep", n)     # operating point
METHOD = col("eval_method", "method_name")
ERROR_TYPE = col("eval_entry_label", "error_type")
TIER = col("eval_entry_label", "detectability")

FINAL = ["Final layered system"]


def header(title, subtitle):
    return textbox(24, 12, 1232, 64, [(title, 20, True, INK), (subtitle, 10, False, INK_2)])


# --- pages -------------------------------------------------------------------
pages = []

# 1. Overview -----------------------------------------------------------------
cw, gap = 236, 13
v = [header("Final layered system - test-period performance",
            "Last 6 fiscal periods: 28,695 journal entries, 1,297 seeded (entry, error type) pairs. "
            "SQL balance check + duplicate self-join + bank reconciliation + XGBoost @ 0.57.")]
for i, (m, label) in enumerate([("Recall", "Recall"), ("Precision", "Precision"), ("F1", "F1"),
                                ("Flags", "Entries flagged"), ("Analyst Hours per Month", "Analyst hours / month")]):
    v.append(card(24 + i * (cw + gap), 88, cw, 104, F(m), label))
v.append(bar(24, 208, 760, 492, "clusteredBarChart", ERROR_TYPE, [(L("Pair Recall"), "Recall")],
             "Recall by error type", "Pair-level: a header carrying two error types counts once for each",
             colors=[BLUE], sort_field=L("Pair Recall")))
v.append(bar(800, 208, 456, 236, "clusteredBarChart", TIER, [(L("Pair Recall"), "Recall")],
             "Recall by detectability tier", "Seeded difficulty, easy to hard",
             colors=[BLUE], sort_field=L("Pair Recall")))
v.append(textbox(800, 460, 456, 240, [
    ("What to read here", 12, True, INK),
    ("Seven of eight error types are caught at 0.79 or better. Backdated is the gap: 0.26 against a "
     "measured design ceiling of 0.84 - the features to close it (lag against the poster's own history, "
     "an interaction over the three risk drivers) are not in the feature table yet.", 10, False, INK_2),
    ("Recall is only half the story: every flag costs ~5 analyst-minutes, so read it next to the "
     "workload card. Figures validated DAX vs SQL (scripts/validate_dax.py).", 10, False, INK_2),
]))
pages.append(("overview", "Overview", v, [in_filter("pgMethodFinal1", "eval_method", "method_name", FINAL)]))

# 2. Layered detection ----------------------------------------------------------
v = [header("Layered detection - who catches what",
            "The final system is L1 OR L2 OR L3 OR L4. First-catch credits the earliest layer in running order "
            "(cheap SQL checks first, the model last) and sums to the system total.")]
v.append(bar(24, 88, 616, 300, "clusteredBarChart", METHOD,
             [(F("First-Catch Flags"), "First-catch flags"), (F("First-Catch True Positives"), "First-catch true positives")],
             "Flags credited to each layer", "Sums to 1,710 flags / 1,073 true positives",
             colors=[ORANGE, BLUE], sort_field=METHOD, sort_dir="Ascending"))
v.append(table(656, 88, 600, 300,
               [(METHOD, "Layer"), (F("Flags"), "Flags"), (F("Unique Flags"), "Only this layer"),
                (F("Unique True Positives"), "Only this layer (TP)")],
               "What removing a layer would cost",
               "Caught by this layer and no other - only 17 of 1,710 flags overlap", sort_field=METHOD))
v.append(matrix(24, 404, 1232, 296, METHOD, ERROR_TYPE, L("First-Catch Pairs Caught"),
                "Error pairs caught first, by layer and error type",
                "Each layer owns different error types: the model catches none of unbalanced, duplicate or unmatched_bank first",
                heat=BLUE))
pages.append(("layers", "Layered detection", v, [in_filter("pgFamLayer2", "eval_method", "method_family", ["layer"])]))

# 3. Method comparison -----------------------------------------------------------
fields = [(METHOD, "Method"), (F("Precision"), "Precision"), (F("Recall"), "Recall"), (F("F1"), "F1"),
          (F("Flag Rate"), "Flag rate"), (F("Analyst Hours per Month"), "Analyst hrs / month")]
v = [header("Method comparison - blind vs labelled",
            "Two scoreboards, deliberately not merged into one ranking: blind methods see no labels, "
            "supervised models were trained on them.")]
v.append(table(24, 88, 608, 232, fields, "Scoreboard A - unsupervised (realistic)",
               "No labels used; best F1 0.27 (segmented IQR)",
               filters=[in_filter("vfBoardA", "eval_method", "scoreboard", ["A"])], sort_field=METHOD))
v.append(table(648, 88, 608, 232, fields, "Scoreboard B - supervised (optimistic)",
               "Trained on labels; LR's recall is bought with an 18% flag rate",
               filters=[in_filter("vfBoardB", "eval_method", "scoreboard", ["B"])], sort_field=METHOD))
v.append(matrix(24, 336, 1232, 364, METHOD, ERROR_TYPE, L("Pair Recall"),
                "Recall by method and error type",
                "Blind methods are amount detectors; models miss what has no feature (unbalanced, duplicate, unmatched_bank)",
                filters=[in_filter("vfFamAB", "eval_method", "method_family", ["unsupervised", "supervised"])],
                heat=BLUE))
pages.append(("methods", "Method comparison", v, []))

# 4. Operating point ---------------------------------------------------------------
THR = col("eval_threshold_sweep", "threshold")
v = [header("Operating point - why XGBoost @ 0.57",
            "Cost model: 5 analyst-minutes per false positive vs 96 expected minutes per miss (480 x 20% escalation) "
            "= 19:1. The cost surface is shallow - treat 0.57 as a band (~0.50-0.60), not a constant.")]
for i, (m, label) in enumerate([("Chosen Threshold", "Chosen threshold"), ("Chosen Recall", "Recall @ threshold"),
                                ("Chosen Precision", "Precision @ threshold"),
                                ("Chosen Analyst Hours per Month", "Analyst hours / month")]):
    v.append(card(24 + i * (296 + 16), 88, 296, 96, S(m), label))
v.append(line(24, 200, 760, 500, THR,
              [(agg("eval_threshold_sweep", "precision"), "Precision"), (agg("eval_threshold_sweep", "recall"), "Recall")],
              "Precision and recall across the threshold grid", "XGBoost alone, 0.01 to 0.99",
              colors=[BLUE, ORANGE]))
v.append(line(800, 200, 456, 244, THR, [(agg("eval_threshold_sweep", "analyst_hours_per_month"), "Analyst hours / month")],
              "Review workload", "False positives x 5 min, per month", colors=[BLUE]))
v.append(line(800, 456, 456, 244, THR, [(agg("eval_threshold_sweep", "expected_cost_minutes"), "Expected cost")],
              "Expected cost (FP-equivalents) at 19:1", "Minimum at 0.57", colors=[ORANGE]))
pages.append(("operating", "Operating point", v, []))

# 5. Workload ------------------------------------------------------------------------
v = [header("Review workload - what an analyst would face",
            "Final layered system. Every flag is a review: true positives are seeded errors found, "
            "false positives are review time spent on clean entries.")]
for i, (m, label) in enumerate([("Flags", "Entries flagged"), ("True Positives", "Errors found"),
                                ("False Positives", "False alarms"), ("Flag Rate", "Share of ledger flagged")]):
    v.append(card(24 + i * (296 + 16), 88, 296, 96, F(m), label))
v.append(bar(24, 200, 760, 468, "columnChart", col("eval_entry", "fiscal_period"),
             [(F("True Positives"), "Errors found"), (F("False Positives"), "False alarms")],
             "Flags per fiscal period", "Stacked: errors found vs false alarms",
             colors=[BLUE, ORANGE], sort_field=col("eval_entry", "fiscal_period"), sort_dir="Ascending",
             labels=False))
v.append(bar(800, 200, 456, 468, "barChart", col("dim_employee", "employee_name"),
             [(F("True Positives"), "Errors found"), (F("False Positives"), "False alarms")],
             "Flags by poster", "Top 15 of 39 posters make 89% of entries",
             colors=[BLUE, ORANGE], sort_field=F("True Positives"), labels=False))
# appended last so every existing visual keeps its name (and its mobile layout)
v.append(textbox(24, 676, 1232, 32, [
    ("Employee names are shown for analysis only; a production deployment would use "
     "role-based access or pseudonymised poster IDs.", 9, False, INK_2)]))
pages.append(("workload", "Review workload", v, [in_filter("pgMethodFinal5", "eval_method", "method_name", FINAL)]))


# --- write -----------------------------------------------------------------------------
# Keep Desktop-authored mobile layouts (visuals/<name>/mobile.json) across a
# regenerate: stash them by page/visual name, rewrite, then put them back for
# any visual that still exists.
mobile = {}
if PAGES.exists():
    for m in PAGES.glob("*/visuals/*/mobile.json"):
        mobile[(m.parts[-4], m.parts[-2])] = m.read_text(encoding="utf-8")
    shutil.rmtree(PAGES)
PAGES.mkdir(parents=True)

order = []
for key, display, visuals, page_filters in pages:
    pname = f"pg_{key}"
    order.append(pname)
    pdir = PAGES / pname
    (pdir / "visuals").mkdir(parents=True)
    page = {"$schema": PAGE_SCHEMA, "name": pname, "displayName": display,
            "displayOption": "FitToPage", "height": H, "width": W}
    if page_filters:
        page["filterConfig"] = {"filters": page_filters}
    (pdir / "page.json").write_text(json.dumps(page, indent=2), encoding="utf-8")
    for i, vc in enumerate(visuals):
        vc["position"]["z"] = i * 1000
        vc["position"]["tabOrder"] = i * 1000
        vdir = pdir / "visuals" / vc["name"]
        vdir.mkdir()
        (vdir / "visual.json").write_text(json.dumps(vc, indent=2), encoding="utf-8")
        if (pname, vc["name"]) in mobile:
            (vdir / "mobile.json").write_text(mobile.pop((pname, vc["name"])), encoding="utf-8")

(PAGES / "pages.json").write_text(json.dumps(
    {"$schema": PAGES_SCHEMA, "pageOrder": order, "activePageName": order[0]}, indent=2), encoding="utf-8")

# The theme is NOT managed here: it is chosen in Desktop (currently the
# built-in "Tidal") and lives in report.json / StaticResources, which this
# script leaves alone. Series colours that carry meaning (errors found = blue,
# false alarms = orange) are set per visual above, so they survive any theme.
if mobile:
    print("dropped mobile layouts for removed visuals:", sorted(mobile))
print(f"wrote {len(pages)} pages, {sum(len(p[2]) for p in pages)} visuals")
