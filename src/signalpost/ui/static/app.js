/* Progressive enhancement only. Every page works with this file blocked:
   filtering, sorting, pagination, comparison and downloads are plain links and
   forms handled on the server. Nothing here invents data — the job poller reads
   the same counted figures the server renders. */

(function () {
  "use strict";

  // -- compare selection: enforce the server's 2-3 limit before the round trip --
  var compareForm = document.querySelector('form[action="/compare"]');
  if (compareForm) {
    var boxes = Array.prototype.slice.call(
      compareForm.querySelectorAll('input[type="checkbox"][name="org"]')
    );
    var submit = compareForm.querySelector('button[type="submit"]');
    var hint = compareForm.querySelector(".actions .meta");
    var MAX = 3;

    var sync = function () {
      var chosen = boxes.filter(function (box) { return box.checked; });
      boxes.forEach(function (box) {
        box.disabled = !box.checked && chosen.length >= MAX;
      });
      if (submit) {
        submit.disabled = chosen.length < 2;
        submit.textContent =
          chosen.length < 2 ? "Compare selected" : "Compare " + chosen.length + " companies";
      }
      if (hint) {
        hint.textContent =
          chosen.length >= MAX
            ? "Three selected, the maximum the comparison view accepts."
            : "Select two or three researched companies.";
      }
    };

    boxes.forEach(function (box) { box.addEventListener("change", sync); });
    sync();
  }

  // -- job progress: poll the server's counted figure while a job is running --
  var jobPanel = document.querySelector("[data-job-id]");
  if (jobPanel && jobPanel.getAttribute("data-job-finished") === "false") {
    var jobId = jobPanel.getAttribute("data-job-id");
    var fill = jobPanel.querySelector(".progress-fill");
    var text = document.getElementById("job-progress-text");
    var attempts = 0;

    var poll = function () {
      attempts += 1;
      if (attempts > 240) { return; }
      fetch("/api/jobs/" + encodeURIComponent(jobId), { headers: { Accept: "application/json" } })
        .then(function (response) { return response.ok ? response.json() : null; })
        .then(function (job) {
          if (!job) { return; }
          var percent = job.total ? Math.floor((job.completed / job.total) * 100) : 0;
          if (fill) { fill.style.width = percent + "%"; }
          if (text) {
            text.textContent =
              job.completed + " of " + job.total + " envelopes written · state: " + job.state;
          }
          if (job.state === "completed" || job.state === "failed") {
            window.location.reload();
            return;
          }
          window.setTimeout(poll, 5000);
        })
        .catch(function () { /* leave the server-rendered figure in place */ });
    };

    window.setTimeout(poll, 5000);
  }
})();
