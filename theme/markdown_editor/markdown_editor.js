import { Editor, StarterKit, Markdown, Placeholder } from "wizzrobe-tiptap";

const TOOLS = [
  { name: "bold", label: "Bold", icon: "format_bold", run: (c) => c.toggleBold(), active: "bold" },
  { name: "italic", label: "Italic", icon: "format_italic", run: (c) => c.toggleItalic(), active: "italic" },
  { name: "code", label: "Inline code", icon: "code", run: (c) => c.toggleCode(), active: "code" },
  { name: "link", label: "Link", icon: "link", active: "link" },
  { sep: true },
  { name: "h2", label: "Heading", icon: "title", run: (c) => c.toggleHeading({ level: 2 }), active: ["heading", { level: 2 }] },
  { name: "h3", label: "Subheading", icon: "text_fields", run: (c) => c.toggleHeading({ level: 3 }), active: ["heading", { level: 3 }] },
  { name: "bullets", label: "Bulleted list", icon: "format_list_bulleted", run: (c) => c.toggleBulletList(), active: "bulletList" },
  { name: "numbers", label: "Numbered list", icon: "format_list_numbered", run: (c) => c.toggleOrderedList(), active: "orderedList" },
  { name: "quote", label: "Note", icon: "format_quote", run: (c) => c.toggleBlockquote(), active: "blockquote" },
  { sep: true },
  { name: "undo", label: "Undo", icon: "undo", run: (c) => c.undo() },
  { name: "redo", label: "Redo", icon: "redo", run: (c) => c.redo() },
];

const COMPACT_TOOLS = ["bold", "italic", "code", "link", "bullets"];

// Must match application/content/blocks.py: `//host` and `/\host` are other sites.
const SAFE_HREF = /^(https:\/\/[^\s\\]+|mailto:\S+|\/(?![\/\\])[^\s\\]*)$/;

export default {
  template: `
    <div class="wiz-md-editor" :class="{ 'wiz-md-editor--disabled': disable, 'wiz-md-editor--compact': compact }">
      <div v-if="label" :id="labelId" class="wiz-md-editor__label">{{ label }}</div>
      <div class="wiz-md-editor__toolbar" role="toolbar" :aria-label="(label || 'Text') + ' formatting'">
        <template v-for="tool in tools">
          <span v-if="tool.sep" class="wiz-md-editor__sep" aria-hidden="true"></span>
          <button v-else type="button" class="wiz-md-editor__btn"
                  :class="{ 'wiz-md-editor__btn--on': isActive(tool) }"
                  :aria-label="tool.label" :title="tool.label"
                  :aria-pressed="tool.active ? String(isActive(tool)) : undefined"
                  :disabled="disable" @mousedown.prevent @click="apply(tool)">
            <i class="q-icon notranslate material-icons" aria-hidden="true">{{ tool.icon }}</i>
          </button>
        </template>
      </div>
      <div ref="content" class="wiz-md-editor__content"></div>
    </div>
  `,
  props: {
    value: String,
    label: String,
    placeholder: String,
    disable: Boolean,
    compact: Boolean,
  },
  data() {
    const tools = this.compact
      ? TOOLS.filter((t) => COMPACT_TOOLS.includes(t.name)).flatMap((t) => (t.name === "bullets" ? [{ sep: true }, t] : [t]))
      : TOOLS;
    return { tools, tick: 0, labelId: `wiz-md-${Math.random().toString(36).slice(2)}` };
  },
  mounted() {
    this.lastEmitted = this.value || "";
    this.editor = new Editor({
      element: this.$refs.content,
      extensions: [
        StarterKit.configure({
          codeBlock: false,
          horizontalRule: false,
          strike: false,
          underline: false,
          heading: { levels: [2, 3] },
          link: {
            openOnClick: false,
            autolink: true,
            protocols: ["mailto"],
            isAllowedUri: (url) => SAFE_HREF.test(url),
          },
        }),
        Markdown,
        Placeholder.configure({ placeholder: this.placeholder || "" }),
      ],
      content: this.value || "",
      contentType: "markdown",
      editable: !this.disable,
      editorProps: {
        attributes: {
          role: "textbox",
          "aria-multiline": "true",
          ...(this.label ? { "aria-labelledby": this.labelId } : { "aria-label": "Text" }),
          class: "wiz-md-editor__prose",
        },
      },
      onUpdate: () => this.scheduleEmit(),
      onSelectionUpdate: () => this.tick++,
      onTransaction: () => this.tick++,
      onBlur: () => this.flush(),
    });
  },
  beforeUnmount() {
    this.flush();
    this.editor?.destroy();
  },
  watch: {
    value(newValue) {
      if ((newValue || "") === this.lastEmitted) return;
      this.lastEmitted = newValue || "";
      this.editor?.commands.setContent(newValue || "", { contentType: "markdown", emitUpdate: false });
    },
    disable(value) {
      this.editor?.setEditable(!value);
    },
  },
  methods: {
    isActive(tool) {
      this.tick;
      if (!this.editor || !tool.active) return false;
      return Array.isArray(tool.active) ? this.editor.isActive(...tool.active) : this.editor.isActive(tool.active);
    },
    apply(tool) {
      if (!this.editor || this.disable) return;
      if (tool.name === "link") return this.editLink();
      tool.run(this.editor.chain().focus()).run();
    },
    editLink() {
      const previous = this.editor.getAttributes("link").href || "";
      const url = window.prompt("Link address (https://…)", previous);
      if (url === null) return;
      const chain = this.editor.chain().focus().extendMarkRange("link");
      if (url.trim() === "") return chain.unsetLink().run();
      if (!SAFE_HREF.test(url.trim())) {
        window.alert("Links must start with https://");
        return;
      }
      chain.setLink({ href: url.trim() }).run();
    },
    scheduleEmit() {
      clearTimeout(this.timer);
      this.timer = setTimeout(() => this.flush(), 300);
    },
    flush() {
      clearTimeout(this.timer);
      if (!this.editor) return;
      const markdown = this.editor.isEmpty ? "" : this.editor.getMarkdown();
      if (markdown === this.lastEmitted) return;
      this.lastEmitted = markdown;
      this.$emit("update:value", markdown);
    },
  },
};
