import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const output = path.join(root, "outputs", "v2_abc_tuning_20260922");
const htmlPath = path.join(root, "reports", "v2_abc_tuning.html");
const html = fs.readFileSync(htmlPath, "utf8");

function requireCheck(condition, message) {
  if (!condition) throw new Error(message);
}

const table = html.match(/<table id="trials">([\s\S]*?)<\/table>/);
requireCheck(table, "Missing trials table");
const headerBlock = table[1].match(/<thead>([\s\S]*?)<\/thead>/)?.[1] ?? "";
const headers = [...headerBlock.matchAll(/<button[^>]*>([^<]+)<span>/g)]
  .map((match) => match[1].trim());
const body = table[1].match(/<tbody>([\s\S]*?)<\/tbody>/)?.[1] ?? "";
const rowHtml = [...body.matchAll(/<tr>([\s\S]*?)<\/tr>/g)].map((match) => match[1]);
const rows = rowHtml.map((raw) => {
  const cells = [...raw.matchAll(/<td[^>]*data-value="([^"]*)"[^>]*>([\s\S]*?)<\/td>/g)];
  requireCheck(cells.length === headers.length, "Cell/header count mismatch");
  return Object.fromEntries(cells.map((cell, index) => [headers[index], {
    value: cell[1],
    text: cell[2].replace(/<[^>]+>/g, "").trim(),
  }]));
});

// Execute the delivered functions with a minimal in-memory document interface.
// This checks the actual inline code without browser navigation or rendering.
const documentRows = rows.map((row) => ({
  cells: headers.map((header) => ({
    dataset: { value: row[header].value }, textContent: row[header].text,
  })),
  textContent: headers.map((header) => row[header].text).join(""),
  style: {},
}));
const tableBody = {
  rows: documentRows,
  appendChild(row) {
    this.rows.splice(this.rows.indexOf(row), 1);
    this.rows.push(row);
  },
};
const elements = {
  trials: { tBodies: [tableBody] },
  query: { value: "" }, strategy: { value: "" }, design: { value: "" },
  shown: { textContent: "" },
};
const context = vm.createContext({ document: {
  getElementById(id) { requireCheck(id in elements, `Unknown element ${id}`); return elements[id]; },
  querySelectorAll(selector) {
    requireCheck(selector === "#trials tbody tr", `Unexpected selector ${selector}`);
    return tableBody.rows;
  },
} });
const inlineScript = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)]
  .map((match) => match[1]).join("\n");
requireCheck(inlineScript.length > 0, "Missing inline script");
vm.runInContext(inlineScript, context, { timeout: 1000 });
function filtered(data, query = "", strategy = "", design = "") {
  elements.query.value = query;
  elements.strategy.value = strategy;
  elements.design.value = design;
  vm.runInContext("filterRows()", context, { timeout: 1000 });
  return tableBody.rows.filter((row) => row.style.display !== "none");
}

requireCheck(rows.length === 192, `Expected 192 table rows, got ${rows.length}`);
for (const strategy of ["A", "B", "C"]) {
  requireCheck(filtered(rows, "", strategy).length === 64, `${strategy} filter mismatch`);
  requireCheck(
    filtered(rows, "", strategy, "one_factor").length === 25,
    `${strategy} one_factor filter mismatch`,
  );
}
requireCheck(filtered(rows, "p005").length === 3, "Text search mismatch for p005");
const returnColumn = headers.indexOf("economic_total_return");
requireCheck(returnColumn >= 0, "Return column missing");
for (const ascending of [true, false]) {
  vm.runInContext(`sortTable('trials', ${returnColumn}, true)`, context, { timeout: 1000 });
  const values = tableBody.rows.map((row) => Number(row.cells[returnColumn].dataset.value));
  requireCheck(values.every(Number.isFinite), "Non-finite sort keys");
  requireCheck(values.every((value, index) => index === 0
    || (ascending ? values[index - 1] <= value : values[index - 1] >= value)),
  `${ascending ? 'Ascending' : 'Descending'} numeric sort failed`);
}
requireCheck(html.includes("function sortTable("), "Missing sortTable JavaScript");
requireCheck(html.includes("function filterRows("), "Missing filterRows JavaScript");
requireCheck((html.match(/data:image\/png;base64,/g) ?? []).length === 4, "Embedded image count mismatch");
requireCheck(!/<script[^>]+src=/.test(html), "External script found");
requireCheck(!/<link[^>]+stylesheet/.test(html), "External stylesheet found");

const monthlyPath = path.join(output, "all_trial_monthly.csv");
const monthlyLines = fs.readFileSync(monthlyPath, "utf8").trimEnd().split(/\r?\n/);
requireCheck(monthlyLines.length === 4033, "Monthly CSV must contain header plus 4,032 rows");

const evidence = {
  status: "PASS",
  validator: "pure Node.js HTML/data check and actual inline functions in vm with in-memory document stubs; no browser navigation or rendering",
  html: path.relative(root, htmlPath),
  checks: {
    main_table_rows: rows.length,
    strategy_filter_rows: { A: 64, B: 64, C: 64 },
    strategy_one_factor_rows: { A: 25, B: 25, C: 25 },
    query_p005_rows: 3,
    numeric_sort_ascending: true,
    numeric_sort_descending: true,
    inline_sort_function_present: true,
    inline_filter_function_present: true,
    actual_inline_functions_executed: true,
    embedded_images: 4,
    external_assets: 0,
    all_trial_monthly_rows: monthlyLines.length - 1,
  },
  browser_preview: "NOT_RUN_URL_POLICY_BLOCKED",
};
const evidencePath = path.join(output, "report_validation.json");
fs.writeFileSync(evidencePath, `${JSON.stringify(evidence, null, 2)}\n`);
console.log(JSON.stringify(evidence, null, 2));
