export interface Post {
  id: number
  user_id: number
  content: string
  created_at: number
  updated_at: number
}

export type WriterRequest =
  | { kind: "write"; requestId: number; content: string; email: string }
  | { kind: "stop" }

export type WriterReply =
  | { kind: "ready" }
  | ({ kind: "post"; requestId: number } & Post)
  | { kind: "error"; requestId: number; message: string }
  | { kind: "stopped" }
