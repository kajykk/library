'use client';

/**
 * [[双链]] 装饰扩展：把正文中的 [[标题]] 渲染为 .kb-wikilink span，
 * 点击行为由编辑器 handleClick（NoteEditor 内）基于 data-title 处理。
 */

import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';

export const wikilinkKey = new PluginKey('kb-wikilink');

function wikilinkDecorations(doc: import('@tiptap/pm/model').Node): DecorationSet {
  const decorations: Decoration[] = [];
  doc.descendants((node: { isText: boolean; text?: string }, pos: number) => {
    if (!node.isText || !node.text) return;
    const regex = /\[\[([^\[\]\n]+)\]\]/g;
    let m: RegExpExecArray | null;
    while ((m = regex.exec(node.text)) !== null) {
      decorations.push(
        Decoration.inline(pos + m.index, pos + m.index + m[0].length, {
          nodeName: 'span',
          class: 'kb-wikilink',
          'data-title': m[1],
        }),
      );
    }
  });
  return DecorationSet.create(doc, decorations);
}

export const WikiLinkExtension = Extension.create({
  name: 'wikilink',
  addProseMirrorPlugins() {
    return [
      new Plugin({
        key: wikilinkKey,
        state: {
          init: (_, state) => wikilinkDecorations(state.doc),
          apply: (tr, old) => (tr.docChanged ? wikilinkDecorations(tr.doc) : old),
        },
        props: {
          decorations(state) {
            return this.getState(state);
          },
        },
      }),
    ];
  },
});
