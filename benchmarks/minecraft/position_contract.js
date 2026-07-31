"use strict";

const ENTITY_FEET = "entity_feet";

function finitePosition(value, name) {
  if (!value || ["x", "y", "z"].some((axis) => typeof value[axis] !== "number" || !Number.isFinite(value[axis]))) {
    throw new TypeError(`${name} must contain finite numeric x, y, and z`);
  }
  return { x: value.x, y: value.y, z: value.z };
}

function observeEntityFeet(position) {
  const entityFeet = finitePosition(position, "entity feet");
  const blockCell = {
    x: Math.floor(entityFeet.x),
    y: Math.floor(entityFeet.y),
    z: Math.floor(entityFeet.z),
  };
  return {
    entity_feet: entityFeet,
    block_cell: blockCell,
    support_block: { x: blockCell.x, y: blockCell.y - 1, z: blockCell.z },
  };
}

function evaluateEntityFeet(position, target, tolerance, convention) {
  if (convention !== ENTITY_FEET) throw new TypeError(`unsupported position convention: ${String(convention)}`);
  if (typeof tolerance !== "number" || !Number.isFinite(tolerance) || tolerance <= 0) {
    throw new TypeError("tolerance must be finite and positive");
  }
  const observed = finitePosition(position, "observed position");
  const requested = finitePosition(target, "requested target");
  const axisDelta = {};
  const remainingDelta = {};
  for (const axis of ["x", "y", "z"]) {
    axisDelta[axis] = Math.abs(observed[axis] - requested[axis]);
    remainingDelta[axis] = requested[axis] - observed[axis];
  }
  return {
    position_convention: ENTITY_FEET,
    requested_target: requested,
    observed_position: observed,
    axis_delta: axisDelta,
    remaining_delta: remainingDelta,
    distance_to_target: Math.sqrt(Object.values(axisDelta).reduce((sum, value) => sum + value ** 2, 0)),
    target_tolerance: tolerance,
    target_reached: Object.values(axisDelta).every((value) => value < tolerance),
    observation: observeEntityFeet(observed),
  };
}

module.exports = { ENTITY_FEET, evaluateEntityFeet, observeEntityFeet };

if (require.main === module) {
  const fs = require("node:fs");
  const fixtures = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
  const results = fixtures.cases.map((item) => {
    try {
      return { id: item.id, result: evaluateEntityFeet(item.observed, item.target, item.tolerance, item.position_convention) };
    } catch (error) {
      return { id: item.id, error: error && error.name ? error.name : "Error" };
    }
  });
  process.stdout.write(`${JSON.stringify({ results })}\n`);
}
