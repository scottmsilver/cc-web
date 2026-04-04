export type ContentBlock = {
  type?: string;
  text?: string;
  thinking?: string;
  name?: string;
  id?: string;
  input?: Record<string, unknown>;
  tool_use_id?: string;
  content?: unknown;
};

export type JsonlEntry = {
  type?: string;
  message?: {
    content?: (ContentBlock | string)[] | string;
    role?: string;
    model?: string;
  };
  [key: string]: unknown;
};

export type QuestionOption = {
  label: string;
  description?: string;
  index: number;
};

export type PendingQuestion = {
  question: string;
  options: QuestionOption[];
};
