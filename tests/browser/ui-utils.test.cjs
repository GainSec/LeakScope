const test = require("node:test");
const assert = require("node:assert/strict");
const ui = require("../../leakscope_app/static/js/leakscope/utils.js");
test("remote links permit only HTTP and HTTPS", () => {
  assert.equal(ui.safeUrl("javascript:alert(1)"), null);
  assert.equal(ui.safeUrl("data:text/html,x"), null);
  assert.equal(ui.safeUrl("https://example.test/a"), "https://example.test/a");
});
test("terminal states stop polling regardless of casing", () => {
  for (const state of [
    "SUCCESS",
    "succeeded",
    "FAILURE",
    "failed",
    "SKIPPED",
    "cancelled",
    "lost",
    "REVOKED",
  ])
    assert.equal(ui.isTerminal(state), true, state);
  assert.equal(ui.isTerminal("running"), false);
});
test("run starts normalize single and multiple contracts", () => {
  assert.deepEqual(ui.startedRuns({ run: { id: "one" } }), [{ id: "one" }]);
  assert.deepEqual(ui.startedRuns({ runs: [{ id: "one" }, { id: "two" }] }), [
    { id: "one" },
    { id: "two" },
  ]);
});
test("masking defaults sensitive field values to concealed", () => {
  assert.equal(ui.isSensitive("secret"), true);
  assert.equal(ui.isSensitive("secrets"), true);
  assert.equal(ui.isSensitive("context"), true);
  assert.equal(ui.isSensitive("ip"), false);
});
test("progress clamps provider values and handles missing totals", () => {
  assert.equal(ui.progress({ progress_current: 9, progress_total: 3 }), 100);
  assert.equal(ui.progress({ progress_current: 1, progress_total: 0 }), null);
});
test('expansion progress stays indeterminate while its frontier grows and paused jobs stop polling',()=>{assert.equal(ui.progress({kind:'domain_expansion',progress_current:21,progress_total:3603}),null);assert.equal(ui.progress({progress_mode:'indeterminate',progress_current:1,progress_total:100}),null);assert.equal(ui.isTerminal('paused'),true);});
