/* 日志尾：每 2.5 秒拉一次本批日志，暂停后不再追加，恢复时补齐。 */
(function () {
  "use strict";

  const POLL_MS = 2500;

  function $(selector) {
    return document.querySelector(selector);
  }

  function renderLine(event) {
    const line = document.createElement("li");
    line.className = "log-line";
    const stamp = document.createElement("span");
    stamp.className = "log-ts";
    stamp.textContent = (event.at_utc || "").replace("T", " ").slice(0, 16);
    const level = document.createElement("span");
    level.className = "log-level log-" + (event.level || "INFO");
    level.textContent = event.level || "INFO";
    const stage = document.createElement("span");
    stage.className = "log-stage";
    stage.textContent = event.stage || "";
    const message = document.createElement("span");
    message.className = "log-msg";
    message.textContent = event.message || "";
    line.append(stamp, level, stage, message);
    return line;
  }

  function bind() {
    const list = $("#log-tail");
    const toggle = $("#log-toggle");
    if (!list) {
      return;
    }
    const state = { following: true, runId: list.dataset.run || "", count: list.children.length };

    function append(events) {
      events.slice(state.count).forEach(function (event) {
        list.appendChild(renderLine(event));
      });
      state.count = events.length;
      if (state.following) {
        list.scrollTop = list.scrollHeight;
      }
    }

    if (toggle) {
      toggle.addEventListener("click", function () {
        state.following = !state.following;
        toggle.textContent = state.following ? "暂停" : "跟随";
        if (state.following) {
          list.scrollTop = list.scrollHeight;
        }
      });
    }

    window.setInterval(async function () {
      if (!state.runId) {
        return;
      }
      try {
        const response = await fetch("/api/log", { headers: { Accept: "application/json" } });
        if (!response.ok) {
          return;
        }
        const payload = await response.json();
        if (payload.run_id !== state.runId) {
          window.location.reload();
          return;
        }
        append(payload.events || []);
      } catch (error) {
        state.following = false;
        if (toggle) {
          toggle.textContent = "跟随";
        }
      }
    }, POLL_MS);

    list.scrollTop = list.scrollHeight;
  }

  document.addEventListener("DOMContentLoaded", bind);
})();
