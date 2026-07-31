"use strict";

const mineflayer = require("mineflayer");
const { pathfinder, Movements, goals } = require("mineflayer-pathfinder");
const readline = require("node:readline");
const { Vec3 } = require("vec3");
const { ENTITY_FEET, evaluateEntityFeet, observeEntityFeet } = require("./position_contract.js");

const [, , host, portText, payloadText] = process.argv;
const payload = JSON.parse(Buffer.from(payloadText, "base64url").toString("utf8"));
if (payload.position_convention !== ENTITY_FEET) throw new TypeError("probe requires entity_feet position convention");
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const blockName = (bot, item) => {
  const block = bot.blockAt(new Vec3(item.x, item.y, item.z));
  return block ? `minecraft:${block.name}` : null;
};

async function main() {
  const bot = mineflayer.createBot({
    host,
    port: Number(portText),
    username: "VAProbe",
    version: "1.19.2",
    auth: "offline",
  });
  bot.loadPlugin(pathfinder);
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("probe spawn timeout")), 30000);
    bot.once("spawn", () => { clearTimeout(timer); resolve(); });
    bot.once("error", reject);
    bot.once("kicked", (reason) => reject(new Error(`probe kicked: ${String(reason).slice(0, 120)}`)));
    bot.once("end", (reason) => reject(new Error(`probe ended: ${String(reason).slice(0, 120)}`)));
  });

  process.stdout.write("READY\n");
  const input = readline.createInterface({ input: process.stdin });
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("probe observe timeout")), 60000);
    input.once("line", (line) => {
      clearTimeout(timer);
      if (line !== "OBSERVE") reject(new Error("invalid probe control message"));
      else resolve();
    });
  });
  input.close();
  await sleep(1000);
  const state = {
    position: { x: bot.entity.position.x, y: bot.entity.position.y, z: bot.entity.position.z },
    position_convention: ENTITY_FEET,
    position_observation: observeEntityFeet(bot.entity.position),
    yaw: (180 - (bot.entity.yaw * 180 / Math.PI) + 360) % 360,
    pitch: -(bot.entity.pitch * 180 / Math.PI),
    dimension: bot.game.dimension.startsWith("minecraft:") ? bot.game.dimension : `minecraft:${bot.game.dimension}`,
    game_mode: bot.game.gameMode,
    inventory: bot.inventory.slots.flatMap((item, slot) => item ? [{ slot, item: `minecraft:${item.name}`, count: item.count }] : []),
    health: bot.health,
    hunger: bot.food,
    time: bot.time.timeOfDay,
    weather: bot.isRaining ? "rain" : "clear",
    hostile_mob_count: Object.values(bot.entities).filter((entity) => entity.type === "mob" && entity.kind === "Hostile mobs").length,
  };
  const blocks = payload.blocks.map((item) => ({ ...item, observed: blockName(bot, item) }));
  const opening = payload.opening.map((item) => ({ ...item, observed: blockName(bot, item) }));
  const movement = new Movements(bot);
  movement.canDig = false;
  movement.allow1by1towers = false;
  bot.pathfinder.setMovements(movement);
  const probes = [];
  for (const target of payload.targets) {
    if (target.position_convention !== ENTITY_FEET) throw new TypeError("probe target requires entity_feet position convention");
    bot.chat(`/execute in minecraft:overworld run tp VAProbe ${payload.initial.x} ${payload.initial.y} ${payload.initial.z} 0 0`);
    await sleep(350);
    let error = null;
    try {
      await bot.pathfinder.goto(new goals.GoalBlock(target.x, target.y, target.z));
    } catch (exc) {
      error = exc && exc.name ? exc.name : "pathfinding_failed";
    }
    await sleep(100);
    const completion = evaluateEntityFeet(bot.entity.position, target, target.tolerance, target.position_convention);
    const supportBlock = bot.blockAt(new Vec3(
      completion.observation.support_block.x,
      completion.observation.support_block.y,
      completion.observation.support_block.z,
    ));
    probes.push({
      variant_id: target.variant_id,
      target,
      position_convention: ENTITY_FEET,
      raw_entity_feet: completion.observed_position,
      normalized_position: completion.observed_position,
      block_cell: completion.observation.block_cell,
      support_block: completion.observation.support_block,
      support_block_type: supportBlock ? `minecraft:${supportBlock.name}` : null,
      support_block_collision_box: supportBlock ? supportBlock.boundingBox : null,
      support_block_shapes: supportBlock ? supportBlock.shapes : null,
      falling: bot.entity.onGround !== true,
      delta: completion.axis_delta,
      remaining_delta: completion.remaining_delta,
      pathfinder_goal: { type: "GoalBlock", x: target.x, y: target.y, z: target.z, position_convention: ENTITY_FEET },
      reachable: !error && completion.target_reached,
      error,
    });
  }
  process.stdout.write(`${JSON.stringify({ state, blocks, opening, probes })}\n`);
  bot.quit("probe complete");
}

main().catch((error) => {
  process.stderr.write(`${error && error.name ? error.name : "ProbeError"}\n`);
  process.exitCode = 1;
});
