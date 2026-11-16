/* 表格：点表头排序（数值列按数值），行号列右对齐样式已由 CSS 处理。 */
(function () {
  "use strict";

  function valueOf(cell) {
    const text = (cell.textContent || "").replace(/,/g, "").trim();
    const parsed = Number(text);
    return Number.isFinite(parsed) && text !== "" ? parsed : null;
  }

  function sortTable(table, index, numeric, asc) {
    const body = table.tBodies[0];
    if (!body) {
      return;
    }
    const rows = Array.from(body.rows).filter(function (row) {
      return row.cells.length > index && !row.querySelector(".hint");
    });
    rows.sort(function (left, right) {
      const a = numeric ? valueOf(left.cells[index]) : (left.cells[index].textContent || "").trim();
      const b = numeric ? valueOf(right.cells[index]) : (right.cells[index].textContent || "").trim();
      if (numeric) {
        if (a === null && b === null) {
          return 0;
        }
        if (a === null) {
          return 1;
        }
        if (b === null) {
          return -1;
        }
        return asc ? a - b : b - a;
      }
      return asc ? String(a).localeCompare(String(b), "zh-Hans-CN") : String(b).localeCompare(String(a), "zh-Hans-CN");
    });
    rows.forEach(function (row) {
      body.appendChild(row);
    });
  }

  function bind(table) {
    const headers = Array.from(table.tHead ? table.tHead.rows[0].cells : []);
    headers.forEach(function (header, index) {
      header.style.cursor = "pointer";
      header.addEventListener("click", function () {
        const numeric = header.dataset.sort === "num" || header.classList.contains("num");
        const asc = header.dataset.asc !== "1";
        headers.forEach(function (item) {
          delete item.dataset.asc;
        });
        header.dataset.asc = asc ? "1" : "0";
        sortTable(table, index, numeric, asc);
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("table[data-sortable]").forEach(bind);
  });
})();
