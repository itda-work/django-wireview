// Set the theme before the first paint (loaded synchronously in <head>, never defer).
// The policy is itda.work's static/js/head-boot.js: localStorage "theme", "light" unless
// the reader chose "dark", no system preference. The docs share itda.work's origin, so a
// reader's choice there holds here too.
(function () {
  var theme = "light";
  try {
    var saved = localStorage.getItem("theme");
    if (saved === "light" || saved === "dark") theme = saved;
  } catch (_) {}
  document.documentElement.classList.toggle("dark", theme === "dark");
})();
