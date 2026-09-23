"""
Hyperion Report Reconciliation Tool
------------------------------------
Automates the manual "3-sheet" dev-vs-prod / uat-vs-prod diff process.

Assumes:
  - Dev/Source report and Prod/Target report have IDENTICAL layout and row order
    (same headers, same row order) — just exported from different environments.
  - Both files are .xlsx.

What it does:
  1. Reads both reports.
  2. Builds a Difference sheet:
       - Numeric cells -> Prod - Dev (0 = match)
       - Text/label cells -> kept as-is if they match, flagged "MISMATCH" if not
  3. Highlights every non-zero / MISMATCH cell in red.
  4. Adds a Summary sheet: total cells checked, number of issues, and a list
     of exact cell references + row/col context for each issue.
  5. Saves one workbook with 4 sheets: Prod | Dev | Difference | Summary
     — so anyone can still eyeball/verify it manually, same as before.

Usage:
    python hyperion_report_diff.py dev_report.xlsx prod_report.xlsx output.xlsx [--sheet SHEET_NAME]

If --sheet is omitted, the first sheet of each workbook is used.
"""

import sys
import argparse
from openpyxl import load_workbook, Workbook
from openpyxl.styles import PatternFill, Font
from openpyxl.utils import get_column_letter

RED_FILL = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
RED_FONT = Font(color="9C0006", bold=True)
HEADER_FONT = Font(bold=True)


def is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def load_sheet_values(path, sheet_name=None):
    wb = load_workbook(path, data_only=True)
    ws = wb[sheet_name] if sheet_name else wb[wb.sheetnames[0]]
    data = []
    for row in ws.iter_rows():
        data.append([cell.value for cell in row])
    return data, ws.max_row, ws.max_column


def copy_plain_sheet(wb, title, data):
    ws = wb.create_sheet(title)
    for r, row in enumerate(data, start=1):
        for c, val in enumerate(row, start=1):
            ws.cell(row=r, column=c, value=val)
        if r == 1:
            for c in range(1, len(row) + 1):
                ws.cell(row=1, column=c).font = HEADER_FONT
    return ws


def build_diff(dev_data, prod_data, tolerance=0.0):
    """Returns (diff_grid, issues) where issues is a list of dicts."""
    max_rows = max(len(dev_data), len(prod_data))
    diff_grid = []
    issues = []

    for r in range(max_rows):
        dev_row = dev_data[r] if r < len(dev_data) else []
        prod_row = prod_data[r] if r < len(prod_data) else []
        max_cols = max(len(dev_row), len(prod_row))
        out_row = []

        for c in range(max_cols):
            d_val = dev_row[c] if c < len(dev_row) else None
            p_val = prod_row[c] if c < len(prod_row) else None
            col_letter = get_column_letter(c + 1)
            cell_ref = f"{col_letter}{r + 1}"

            if r == 0:
                # header row — keep label, flag if headers don't match
                if d_val == p_val:
                    out_row.append(p_val)
                else:
                    out_row.append(f"MISMATCH: dev='{d_val}' prod='{p_val}'")
                    issues.append({"cell": cell_ref, "type": "header mismatch",
                                    "dev": d_val, "prod": p_val})
                continue

            if is_number(d_val) and is_number(p_val):
                delta = round(p_val - d_val, 6)
                out_row.append(delta)
                if abs(delta) > tolerance:
                    issues.append({"cell": cell_ref, "type": "numeric diff",
                                    "dev": d_val, "prod": p_val, "delta": delta})
            elif d_val == p_val:
                out_row.append(0 if (d_val is None and p_val is None) else p_val if not is_number(p_val) else 0)
                if d_val is None and p_val is None:
                    out_row[-1] = None
            else:
                out_row.append(f"MISMATCH: dev='{d_val}' prod='{p_val}'")
                issues.append({"cell": cell_ref, "type": "value mismatch",
                                "dev": d_val, "prod": p_val})

        diff_grid.append(out_row)

    return diff_grid, issues


def write_diff_sheet(wb, diff_grid):
    ws = wb.create_sheet("Difference")
    for r, row in enumerate(diff_grid, start=1):
        for c, val in enumerate(row, start=1):
            cell = ws.cell(row=r, column=c, value=val)
            flagged = False
            if isinstance(val, str) and val.startswith("MISMATCH"):
                flagged = True
            elif is_number(val) and val != 0:
                flagged = True
            if flagged:
                cell.fill = RED_FILL
                cell.font = RED_FONT
        if r == 1:
            for c in range(1, len(row) + 1):
                ws.cell(row=1, column=c).font = HEADER_FONT
    return ws


def write_summary_sheet(wb, issues, dev_path, prod_path, total_cells):
    ws = wb.create_sheet("Summary", 0)  # put first
    ws["A1"] = "Hyperion Report Reconciliation Summary"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A3"] = "Dev/Source file:"
    ws["B3"] = dev_path
    ws["A4"] = "Prod/Target file:"
    ws["B4"] = prod_path
    ws["A5"] = "Total cells compared:"
    ws["B5"] = total_cells
    ws["A6"] = "Total issues found:"
    ws["B6"] = len(issues)
    ws["A6"].font = Font(bold=True)
    ws["B6"].font = Font(bold=True, color="9C0006" if issues else "006100")
    ws["A7"] = "Status:"
    ws["B7"] = "❌ MISMATCHES FOUND — review below" if issues else "✅ ALL MATCH — no differences"

    start = 9
    ws.cell(row=start, column=1, value="Cell").font = HEADER_FONT
    ws.cell(row=start, column=2, value="Issue Type").font = HEADER_FONT
    ws.cell(row=start, column=3, value="Dev Value").font = HEADER_FONT
    ws.cell(row=start, column=4, value="Prod Value").font = HEADER_FONT
    ws.cell(row=start, column=5, value="Delta").font = HEADER_FONT

    for i, issue in enumerate(issues, start=start + 1):
        ws.cell(row=i, column=1, value=issue["cell"])
        ws.cell(row=i, column=2, value=issue["type"])
        ws.cell(row=i, column=3, value=issue.get("dev"))
        ws.cell(row=i, column=4, value=issue.get("prod"))
        ws.cell(row=i, column=5, value=issue.get("delta", ""))
        for c in range(1, 6):
            ws.cell(row=i, column=c).fill = RED_FILL

    for col, width in zip("ABCDE", [10, 18, 20, 20, 12]):
        ws.column_dimensions[col].width = width
    return ws


def main():
    parser = argparse.ArgumentParser(description="Diff a Dev/Source Hyperion report against a Prod/Target report.")
    parser.add_argument("dev_file", help="Path to Dev/Source .xlsx report")
    parser.add_argument("prod_file", help="Path to Prod/Target .xlsx report")
    parser.add_argument("output_file", help="Path to write the output .xlsx")
    parser.add_argument("--sheet", default=None, help="Sheet name to compare (default: first sheet)")
    parser.add_argument("--tolerance", type=float, default=0.0, help="Numeric tolerance before flagging (default 0)")
    args = parser.parse_args()

    dev_data, _, _ = load_sheet_values(args.dev_file, args.sheet)
    prod_data, _, _ = load_sheet_values(args.prod_file, args.sheet)

    diff_grid, issues = build_diff(dev_data, prod_data, args.tolerance)
    total_cells = sum(len(row) for row in diff_grid)

    wb = Workbook()
    wb.remove(wb.active)  # remove default empty sheet
    copy_plain_sheet(wb, "Prod", prod_data)
    copy_plain_sheet(wb, "Dev", dev_data)
    write_diff_sheet(wb, diff_grid)
    write_summary_sheet(wb, issues, args.dev_file, args.prod_file, total_cells)

    wb.save(args.output_file)
    print(f"Done. {len(issues)} issue(s) found out of {total_cells} cells compared.")
    print(f"Output saved to: {args.output_file}")


if __name__ == "__main__":
    main()
