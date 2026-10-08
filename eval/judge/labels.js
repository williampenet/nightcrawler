// Labels for the judge_taste eval (WIP-57): the taste eval's own labels and leave-one-out
// rule-based scores (eval/taste/run.js scoreLabels), so both evals judge the same concerts.
// Reads {state, feedback, concerts, artists} on stdin; writes {labels: [{id, label, rule}]} on
// stdout for eval/judge/__main__.py only (a pipe, never printed to the log: ids are personal).
"use strict";

const { scoreLabels } = require("../taste/run.js");

const chunks = [];
process.stdin.on("data", (d) => chunks.push(d));
process.stdin.on("end", () => {
  let input;
  try {
    input = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    process.stderr.write("judge labels: invalid JSON on stdin\n"); // never echoes the input
    process.exit(2);
  }
  const { scored } = scoreLabels(input);
  const labels = scored.map(({ id, label, score }) => ({ id, label, rule: score }));
  process.stdout.write(JSON.stringify({ labels }) + "\n");
});
