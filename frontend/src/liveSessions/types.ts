export type SessionStatus =
  | 'draft'
  | 'live'
  | 'paused'
  | 'processing'
  | 'ready'
  | 'archived';

export type BriefStatus =
  | 'idle'
  | 'waiting'
  | 'writing'
  | 'ready'
  | 'failed'
  | 'empty';

export type TrackKind = 'local' | 'remote' | 'mixed';

export interface LiveSegment {
  id: string;
  track: TrackKind;
  start_ms: number;
  end_ms: number;
  text: string;
  speaker?: string | null;
  is_echo?: boolean;
  confidence?: number | null;
}

export interface LiveSessionCard {
  id: string;
  title: string;
  status: SessionStatus;
  language: string;
  source_app?: string | null;
  participants: string[];
  started_at?: string | null;
  ended_at?: string | null;
  duration_ms: number;
  brief_status: BriefStatus;
  preview: string;
  trashed_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface LiveSessionDetail extends LiveSessionCard {
  user_notes: string;
  brief_markdown: string;
  local_audio_key?: string | null;
  remote_audio_key?: string | null;
  mixed_audio_key?: string | null;
  keep_audio: boolean;
  metadata: Record<string, unknown>;
  segments: LiveSegment[];
  collection_ids: string[];
}

export interface LiveCollection {
  id: string;
  name: string;
  color?: string | null;
  icon?: string | null;
  sort_order: number;
  created_at?: string | null;
  updated_at?: string | null;
}
