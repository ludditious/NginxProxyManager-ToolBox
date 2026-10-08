// Copyright (C) 2026 https://ludditious.com/
//
//     This program is free software: you can redistribute it and/or modify
//     it under the terms of the GNU Affero General Public License as published by
//     the Free Software Foundation, either version 3 of the License, or
//     (at your option) any later version.
//
//     This program is distributed in the hope that it will be useful,
//     but WITHOUT ANY WARRANTY; without even the implied warranty of
//     MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
//     GNU Affero General Public License for more details.
//
//     You should have received a copy of the GNU Affero General Public License
//     along with this program.  If not, see <https://www.gnu.org/licenses/>.

document.querySelectorAll(".pw-toggle").forEach((btn) => {
  btn.addEventListener("click", () => {
    const row = btn.closest(".password-row");
    if (!row) return;
    const input = row.querySelector(".pw-field");
    if (!input) return;
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    btn.textContent = show ? "Hide" : "Show";
  });
});

(function initThemeToggle() {
  const btn = document.getElementById("theme-toggle");
  if (!btn) return;

  function currentTheme() {
    return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
      localStorage.setItem("npm-toolbox-theme", theme);
    btn.textContent = theme === "light" ? "Dark" : "Light";
    btn.setAttribute(
      "aria-label",
      theme === "light" ? "Switch to dark theme" : "Switch to light theme"
    );
  }

  applyTheme(currentTheme());

  btn.addEventListener("click", () => {
    applyTheme(currentTheme() === "light" ? "dark" : "light");
  });
})();

(function initPortPreset() {
  document.querySelectorAll("form").forEach((form) => {
    const sel = form.querySelector(".port-preset-select");
    const wrap = form.querySelector(".custom-port-field");
    const custom = form.querySelector(".npm-port-custom");
    if (!sel || !wrap) return;
    function sync() {
      const customOn = sel.value === "custom";
      wrap.hidden = !customOn;
      if (custom) custom.required = customOn;
    }
    sel.addEventListener("change", sync);
    sync();
  });
})();

document.querySelectorAll(".copy-cmd").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const id = btn.getAttribute("data-target");
    const el = id ? document.getElementById(id) : null;
    if (!el) return;
    const text = "value" in el ? el.value : el.textContent || "";
    try {
      await navigator.clipboard.writeText(text.trimEnd());
      const prev = btn.textContent;
      btn.textContent = "Copied";
      setTimeout(() => {
        btn.textContent = prev;
      }, 2000);
    } catch {
      el.focus();
      el.select();
    }
  });
});
