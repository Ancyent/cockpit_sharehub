/* SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) 2026 cockpit_sharehub contributors */
/* Versions & restore: browse read-only snapshots and the protected recycle bin of a share. */
"use strict";

(function () {
    const h = SH.h;
    const C = SH.controls;

    const fmtTime = iso => new Date(iso).toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    const fmtEpoch = s => new Date(s * 1000).toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

    async function restore(name, payload, label) {
        const data = await SH.call("versions-restore", Object.assign({ name }, payload));
        SH.toast(`Restored ${label} as "${data.restored}".`);
        return data;
    }

    function snapshotsPanel(name, info, reload) {
        const box = h("div", { class: "form" });
        if (!info.versioning.snapshots && !info.snapshots.length) {
            box.append(C.empty("Scheduled versions are off for this share. Turn them on in Edit → Versions (needs ZFS or Btrfs)."));
            return box;
        }
        const listBox = h("div", { class: "version-files" });
        const crumbs = h("div", { class: "crumbs" });
        let snapshot = info.snapshots.length ? info.snapshots[0].name : null;
        let path = "";
        const picker = C.select({
            label: "Version", searchable: info.snapshots.length > 10, value: snapshot,
            options: info.snapshots.map((s, i) => ({ value: s.name, label: fmtTime(s.time) + (i === 0 ? "  (latest)" : "") })),
            onChange: v => { snapshot = v; path = ""; load(); },
        });
        const now = C.button("Create a version now", async () => {
            try {
                await SH.call("versions-create", { name });
                SH.toast("Version created.");
                reload("versions");
            } catch (e) { SH.toast(e.message, "danger"); }
        }, "secondary");
        async function load() {
            if (!snapshot) { listBox.replaceChildren(C.empty("No versions yet. The first one is taken within 15 minutes, or create one now.")); return; }
            listBox.replaceChildren(h("p", { class: "muted pad" }, "Loading…"));
            try {
                const data = await SH.call("versions-browse", { name, snapshot, path });
                const parts = data.path ? data.path.split("/") : [];
                crumbs.replaceChildren(
                    C.button(name, () => { path = ""; load(); }, "link"),
                    ...parts.map((p, i) => [h("span", { class: "muted" }, " / "), C.button(p, () => { path = parts.slice(0, i + 1).join("/"); load(); }, "link")]).flat());
                if (!data.entries.length) { listBox.replaceChildren(C.empty("This folder was empty in this version.")); return; }
                listBox.replaceChildren(h("table", { class: "tbl tbl-compact" },
                    h("thead", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "Size"), h("th", {}, "Modified"), h("th", { class: "col-actions" }, h("span", { class: "sr-only" }, "Actions")))),
                    h("tbody", {}, data.entries.map(e => {
                        const rel = (data.path ? data.path + "/" : "") + e.name;
                        return h("tr", {},
                            h("td", {}, e.type === "dir"
                                ? C.button("📁 " + e.name, () => { path = rel; load(); }, "link", { "aria-label": `Open folder ${e.name}` })
                                : h("span", {}, e.name)),
                            h("td", { class: "small" }, e.type === "dir" ? "-" : SH.bytes(e.size)),
                            h("td", { class: "small muted" }, fmtEpoch(e.mtime)),
                            h("td", { class: "col-actions" }, C.button("Restore", async () => {
                                const ok = await C.confirm({ title: `Restore ${e.name}`, message: [`Copy "${rel}" from the version of ${fmtTime(info.snapshots.find(s => s.name === snapshot).time)} back into the share.`, "If it still exists, the current file is kept and the restored copy gets the version's date in its name."], confirmLabel: "Restore" });
                                if (ok) { try { await restore(name, { snapshot, path: rel }, e.name); } catch (err) { SH.toast(err.message, "danger"); } }
                            }, "link")));
                    }))));
            } catch (e) { listBox.replaceChildren(C.alert("danger", "Could not open this version.", e.message)); }
        }
        box.append(h("div", { class: "grid-2 align-end" }, picker.el, h("div", {}, now)),
            h("p", { class: "muted small" }, `${SH.plural(info.snapshots.length, "version")} kept. Versions are read-only: nobody can change them from SMB, NFS or FTP.`),
            crumbs, listBox);
        load();
        return box;
    }

    function recyclePanel(name, info, reload) {
        if (!info.recycle_enabled) return C.empty("The recycle bin is off for this share. Turn it on in Edit → SMB.");
        if (!info.recycle.length) return C.empty("No deleted files are kept right now.");
        const search = h("input", { type: "text", class: "input search", placeholder: "Filter by name or user…", "aria-label": "Filter deleted files" });
        const body = h("tbody");
        function fill() {
            const q = search.value.trim().toLowerCase();
            const items = info.recycle.filter(i => !q || i.path.toLowerCase().includes(q) || i.user.toLowerCase().includes(q));
            body.replaceChildren(...items.slice(0, 500).map(i => h("tr", {},
                h("td", { class: "mono small" }, i.path),
                h("td", {}, i.user),
                h("td", { class: "small muted" }, fmtEpoch(i.deleted)),
                h("td", { class: "small" }, SH.bytes(i.size)),
                h("td", { class: "col-actions" }, C.button("Restore", async () => {
                    try { await restore(name, { item: i.id }, i.path); reload("recycle"); } catch (e) { SH.toast(e.message, "danger"); }
                }, "link")))));
            if (!items.length) body.appendChild(h("tr", {}, h("td", { colspan: "5", class: "muted center" }, "No match.")));
        }
        search.addEventListener("input", fill);
        fill();
        return h("div", { class: "form" },
            h("p", { class: "muted small" }, "Deleted over SMB, newest first. Restoring puts the file back where it was; an existing file with the same name is kept."),
            search,
            h("table", { class: "tbl tbl-compact" }, h("thead", {}, h("tr", {}, ["File", "Deleted by", "Deleted", "Size", ""].map(t => h("th", {}, t)))), body));
    }

    SH.views.versions = {
        async open(name, tab) {
            let info;
            try { info = await SH.call("versions-list", { name }); } catch (e) { SH.toast(e.message, "danger"); return; }
            const panel = h("div", { class: "tab-panel" });
            const items = [{ id: "versions", label: "Versions", badge: String(info.snapshots.length) }, { id: "recycle", label: "Deleted files", badge: String(info.recycle.length) }];
            let current = tab || (info.versioning.snapshots || info.snapshots.length ? "versions" : "recycle");
            const close = C.button("Close", () => m.close(), "secondary");
            const tabs = C.tabs({ items, active: current, small: true, onChange: id => { current = id; draw(); } });
            const m = C.modal({ title: `Versions of ${name}`, body: h("div", {}, tabs.el, panel), actions: [close], size: "lg" });
            const reload = async which => { m.close(); await SH.views.versions.open(name, which); };
            function draw() {
                panel.replaceChildren(current === "versions" ? snapshotsPanel(name, info, reload) : recyclePanel(name, info, reload));
            }
            draw();
        },
    };
})();
