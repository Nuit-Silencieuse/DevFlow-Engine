import type {
  CreatePipelineRequest,
  PipelineStatusResponse,
  RepositoryContext,
  StageStatusResponse,
} from "./types";

export const STAGE_OPTIONS = [
  { name: "REQUIREMENT_ANALYSIS", label: "需求分析" },
  { name: "SYSTEM_DESIGN", label: "方案设计" },
  { name: "CODE_GENERATION", label: "代码生成" },
  { name: "TEST_GENERATION", label: "测试生成" },
  { name: "CODE_REVIEW", label: "代码评审" },
  { name: "DELIVERY_INTEGRATION", label: "交付集成" },
] as const;

export const DEFAULT_STAGE_NAMES = ["REQUIREMENT_ANALYSIS", "SYSTEM_DESIGN", "CODE_GENERATION"];

export interface PipelineFormValues {
  name: string;
  requirement: string;
  stages: string[];
  rootPath: string;
  includePaths: string;
  excludePaths: string;
  targetFiles: string;
  maxFiles: string;
  maxBytes: string;
}

export function buildCreatePipelineRequest(values: PipelineFormValues): CreatePipelineRequest {
  const request: CreatePipelineRequest = {
    name: values.name.trim(),
    requirement: values.requirement.trim(),
    stages: values.stages.length > 0 ? values.stages : [...DEFAULT_STAGE_NAMES],
  };

  const repository = buildRepositoryContext(values);
  if (repository) {
    request.repository = repository;
  }
  return request;
}

export function buildRepositoryContext(values: PipelineFormValues): RepositoryContext | undefined {
  /*
   * repository 是可选能力：用户没有填写 rootPath 时，前端不传空对象给后端。
   * 这样可以区分“本次不提供代码库上下文”和“提供了上下文但字段非法”两个语义，
   * 后者交给控制平面继续按契约返回 400，避免 UI 自己复制后端校验规则。
   */
  const rootPath = values.rootPath.trim();
  if (!rootPath) {
    return undefined;
  }
  return {
    rootPath,
    includePaths: parsePathList(values.includePaths),
    excludePaths: parsePathList(values.excludePaths),
    targetFiles: parsePathList(values.targetFiles),
    maxFiles: parsePositiveInteger(values.maxFiles, 200),
    maxBytes: parsePositiveInteger(values.maxBytes, 1_048_576),
  };
}

export function parsePathList(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function parsePositiveInteger(value: string, fallback: number): number {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

export function selectReviewStage(
  pipeline: PipelineStatusResponse | null,
  preferredStageName?: string | null,
): StageStatusResponse | null {
  if (!pipeline || pipeline.stages.length === 0) {
    return null;
  }
  if (preferredStageName) {
    const preferred = pipeline.stages.find((stage) => stage.name === preferredStageName);
    if (preferred) {
      return preferred;
    }
  }
  const current = pipeline.stages.find((stage) => stage.name === pipeline.currentStage);
  if (current) {
    return current;
  }
  return pipeline.stages.find((stage) => stage.requiresHumanApproval) ?? pipeline.stages[0];
}

export function hasArtifact(stage: StageStatusResponse | null): boolean {
  return !!stage && !!stage.output && Object.keys(stage.output).length > 0;
}

export function formatJson(value: unknown): string {
  return JSON.stringify(value ?? {}, null, 2);
}

export function stageLabel(stageName: string): string {
  return STAGE_OPTIONS.find((stage) => stage.name === stageName)?.label ?? stageName;
}

export function statusTone(status: string): "neutral" | "active" | "success" | "warning" | "danger" {
  switch (status) {
    case "RUNNING":
      return "active";
    case "COMPLETED":
      return "success";
    case "SUSPENDED":
      return "warning";
    case "FAILED":
    case "REJECTED":
      return "danger";
    default:
      return "neutral";
  }
}
