"use strict";

const mineflayer = require("mineflayer");
const { pathfinder, Movements, goals } = require("mineflayer-pathfinder");
const readline = require("node:readline");
const { Vec3 } = require("vec3");

const [, , host, portText, payloadText] = process.argv;
const payload = JSON.parse(Buffer.from(payloadText, "base64url").toString("utf8"));
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
    bot.chat(`/execute in minecraft:overworld run tp VAProbe ${payload.initial.x} ${payload.initial.y} ${payload.initial.z} 0 0`);
    await sleep(350);
    let error = null;
    try {
      await bot.pathfinder.goto(new goals.GoalBlock(target.x, target.y, target.z));
    } catch (exc) {
      error = exc && exc.name ? exc.name : "pathfinding_failed";
    }
    const position = bot.entity.position;
    const delta = {
      x: Math.abs(position.x - target.x),
      y: Math.abs(position.y - target.y),
      z: Math.abs(position.z - target.z),
    };
    probes.push({ variant_id: target.variant_id, target, delta, reachable: !error && [delta.x, delta.y, delta.z].every((value) => value < target.tolerance), error });
  }
  process.stdout.write(`${JSON.stringify({ state, blocks, opening, probes })}\n`);
  bot.quit("probe complete");
}

main().catch((error) => {
  process.stderr.write(`${error && error.name ? error.name : "ProbeError"}\n`);
  process.exitCode = 1;
});
