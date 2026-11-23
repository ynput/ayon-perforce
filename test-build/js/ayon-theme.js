/*
 * Header behaviour of the AYON theme, like AYONpedia:
 *   - the theme picker menu (partials/palette.html): open, close, mark the choice;
 *   - Ctrl+K (Cmd+K on macOS) focuses the search box, with the keys shown in it.
 */
(function () {
    /*
     * Mermaid: Material draws each diagram in a closed shadow root, out of reach
     * of the page's CSS. Mermaid 11 puts arrow labels in a half-transparent box a
     * little wider than the label, which shows as thin lines on its sides;
     * AYONpedia's Mermaid has no such box. Give every diagram root a sheet that
     * clears it (adopted sheets survive Material writing the SVG into the root).
     */
    if ("adoptedStyleSheets" in Document.prototype) {
        const diagramSheet = new CSSStyleSheet();
        diagramSheet.replaceSync(".labelBkg { background-color: transparent !important; }");
        const attachShadow = Element.prototype.attachShadow;
        Element.prototype.attachShadow = function (init) {
            const root = attachShadow.call(this, init);
            // only Material's diagrams use shadow roots here, and the rule only
            // matches Mermaid's label box
            root.adoptedStyleSheets = [...root.adoptedStyleSheets, diagramSheet];
            return root;
        };
    }

    const picker = document.querySelector(".ayon-theme__picker");
    if (picker) {
        const inputs = Array.from(picker.querySelectorAll("input[name=__palette]"));
        const buttons = Array.from(picker.querySelectorAll(".ayon-theme__button"));
        const menu = picker.querySelector(".ayon-theme__menu");

        // Material applies the stored choice but doesn't check its input
        const stored = __md_get("__palette");
        const index = stored && inputs[stored.index] ? stored.index : 0;
        inputs[index].checked = true;

        const setOpen = (open) => {
            menu.hidden = !open;
            buttons.forEach((button) => button.setAttribute("aria-expanded", String(open)));
        };

        buttons.forEach((button) => button.addEventListener("click", () => setOpen(menu.hidden)));
        inputs.forEach((input) => input.addEventListener("change", () => setOpen(false)));

        // Material's palette form cycles the theme on Enter; the menu handles it instead
        picker.addEventListener("keydown", (event) => {
            if (event.key === "Enter") event.stopPropagation();
            if (event.key === "Escape") setOpen(false);
        });
        document.addEventListener("click", (event) => {
            if (!picker.contains(event.target)) setOpen(false);
        });
    }

    const form = document.querySelector(".md-search__form");
    const query = document.querySelector(".md-search__input");
    if (form && query) {
        const mac = /Mac|iPhone|iPad/.test(navigator.platform);
        const keys = document.createElement("span");
        keys.className = "ayon-search__keys";
        keys.setAttribute("aria-hidden", "true");
        keys.innerHTML = `<kbd>${mac ? "⌘" : "ctrl"}</kbd><kbd>K</kbd>`;
        form.appendChild(keys);

        document.addEventListener("keydown", (event) => {
            if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
                event.preventDefault();
                query.focus();
            }
        });
    }
})();
