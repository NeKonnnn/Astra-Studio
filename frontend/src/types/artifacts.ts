/** Типы артефактов чата (протокол :::artifact). */

export type ArtifactMimeType =
  | 'text/html'
  | 'image/svg+xml'
  | 'text/markdown'
  | 'text/md'
  | 'text/css'
  | 'text/javascript'
  | 'text/x-python'
  | 'application/vnd.mermaid'
  | 'application/vnd.react'
  | 'application/vnd.ant.react'
  | 'application/vnd.code-html'
  | string;

export interface ChatArtifact {
  id: string;
  identifier: string;
  type: ArtifactMimeType;
  title: string;
  content: string;
  /** Блок полностью закрыт (есть :::). */
  closed: boolean;
  messageId?: string;
}

export type ArtifactContentSegment =
  | { kind: 'text'; text: string }
  | { kind: 'artifact'; artifact: ChatArtifact };

export type ArtifactPanelTab = 'preview' | 'code';
