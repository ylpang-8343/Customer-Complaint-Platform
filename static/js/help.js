// Filters the "How do I...?" list as the user types.
(function () {
  const search = document.getElementById("task-search");
  const count = document.getElementById("task-count");
  if (!search) return;
  const items = Array.from(document.querySelectorAll("#tasks details"));

  search.addEventListener("input", () => {
    const words = search.value.toLowerCase().split(/\s+/).filter(Boolean);
    let shown = 0;
    items.forEach((item) => {
      const text = item.textContent.toLowerCase();
      const match = words.every((word) => text.includes(word));
      item.hidden = !match;
      if (match) shown += 1;
    });
    count.textContent = shown;
  });
})();
