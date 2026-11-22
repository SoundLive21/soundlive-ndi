/* SoundLive-NDI : tests sans navigateur du générateur de commandes lecteur. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const html = fs.readFileSync("tableau.html", "utf8");
const script = html.match(/<script>([\s\S]*?)<\/script>/);
assert.ok(script, "Le script de l'interface doit être présent.");
new Function(script[1]); // Analyse syntaxique de l'intégralité du JavaScript du tableau.

const begin = script[1].indexOf("function bashArg(value)");
const end = script[1].indexOf('pairForm.addEventListener("change"', begin);
assert.ok(begin >= 0 && end > begin, "Générateur des commandes absent.");

const installationCommand = vm.runInNewContext(
  script[1].slice(begin, end) + "\ninstallationCommand",
  {}
);
const defaults = {
  name: "Écran test", regie: "192.168.56.1", hostname: "ndi-test-01",
  address: "192.168.56.21/24", gateway: "192.168.56.1",
  dns: "1.1.1.1", dns2: "", ethernet: "", wifi_interface: "",
  wifi: "prompt", ssid: "", installation: "static"
};

for (const kind of ["pi", "ubuntu", "windows"]) {
  for (const ssid of ["", "Ecrans"]) {
    const command = installationCommand(
      {...defaults, kind, ssid}, 8765, "ASSOCIATION_CODE", "a".repeat(64)
    );
    const wifiFlag = kind === "windows" ? "'-Wifi'" : "--wifi";
    assert.equal(command.includes(wifiFlag), Boolean(ssid), kind + " / SSID=" + ssid);
    assert.ok(command.includes("192.168.56.1:8765"), command);
    assert.ok(command.includes("ASSOCIATION_CODE"), command);
    if (kind === "windows") {
      assert.ok(command.startsWith("& 'powershell.exe' "), "L'opérateur PowerShell & est obligatoire");
      assert.ok(command.includes("preparer-lecteur-windows.ps1"), command);
    } else {
      assert.ok(command.includes("--preparer"), command);
    }
    console.log("OK: " + kind + (ssid ? " Ethernet + Wi-Fi" : " Ethernet seul"));
  }
}
const basic = installationCommand(
  {...defaults, kind:"windows", installation:"basic"}, 8765, "ASSOCIATION_CODE", ""
);
assert.ok(basic.startsWith(".\\installer-client.cmd "), basic);
console.log("OK: association Windows simple non modifiée.");
