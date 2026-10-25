// Компиляция смарт-контракта в blockchain/CFAPlatform.json (ABI + байткод).
// Нужно только если вы меняете CFAPlatform.sol — готовый JSON уже лежит в репозитории.
//
//   cd blockchain
//   npm install solc@0.8.24
//   node compile.js
const fs = require("fs");
const path = require("path");
const solc = require("solc");

const source = fs.readFileSync(path.join(__dirname, "CFAPlatform.sol"), "utf8");
const input = {
  language: "Solidity",
  sources: { "CFAPlatform.sol": { content: source } },
  settings: {
    optimizer: { enabled: true, runs: 200 },
    evmVersion: "paris", // без PUSH0 — совместимо с любыми EVM-сетями, включая py-evm и Besu
    outputSelection: { "*": { "*": ["abi", "evm.bytecode.object"] } },
  },
};

const out = JSON.parse(solc.compile(JSON.stringify(input)));
const errors = (out.errors || []).filter((e) => e.severity === "error");
(out.errors || []).forEach((e) => console.error(e.formattedMessage));
if (errors.length) process.exit(1);

const c = out.contracts["CFAPlatform.sol"].CFAPlatform;
const artifact = {
  contractName: "CFAPlatform",
  compiler: "solc " + solc.version(),
  evmVersion: "paris",
  abi: c.abi,
  bytecode: "0x" + c.evm.bytecode.object,
};
fs.writeFileSync(path.join(__dirname, "CFAPlatform.json"), JSON.stringify(artifact, null, 2));
console.log("OK: CFAPlatform.json, байткод", c.evm.bytecode.object.length / 2, "байт");
