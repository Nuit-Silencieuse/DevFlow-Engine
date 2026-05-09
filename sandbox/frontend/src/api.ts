import type {
  CheckpointDecision,
  CheckpointDecisionResponse,
  CreatePipelineRequest,
  CreatePipelineResponse,
  LlmCredentialResponse,
  LlmConfigFileResponse,
  LlmProviderConfig,
  LlmRuntimeConfig,
  LlmConfigTestResponse,
  PipelineSummaryResponse,
  PipelineStatusResponse,
  StageArtifactResponse,
} from "./types";

type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

export class PipelineApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly body: unknown,
  ) {
    super(message);
    this.name = "PipelineApiError";
  }
}

export class PipelineApiClient {
  constructor(
    private readonly baseUrl = "/api/v1",
    private readonly fetchImpl: FetchLike = globalThis.fetch.bind(globalThis),
  ) {}

  createPipeline(request: CreatePipelineRequest): Promise<CreatePipelineResponse> {
    return this.request<CreatePipelineResponse>("/pipelines", {
      method: "POST",
      body: JSON.stringify(request),
    });
  }

  getPipeline(pipelineId: string): Promise<PipelineStatusResponse> {
    return this.request<PipelineStatusResponse>(`/pipelines/${encodeURIComponent(pipelineId)}`);
  }

  getPipelineSummary(pipelineId: string): Promise<PipelineSummaryResponse> {
    return this.request<PipelineSummaryResponse>(`/pipelines/${encodeURIComponent(pipelineId)}/summary`);
  }

  getStageArtifact(pipelineId: string, stageName: string): Promise<StageArtifactResponse> {
    return this.request<StageArtifactResponse>(
      `/pipelines/${encodeURIComponent(pipelineId)}/stages/${encodeURIComponent(stageName)}/artifact`,
    );
  }

  submitCheckpointDecision(
    pipelineId: string,
    stageName: string,
    decision: CheckpointDecision,
    feedback: string,
  ): Promise<CheckpointDecisionResponse> {
    return this.request<CheckpointDecisionResponse>(
      `/pipelines/${encodeURIComponent(pipelineId)}/checkpoints/${encodeURIComponent(stageName)}`,
      {
        method: "POST",
        body: JSON.stringify({ decision, feedback }),
      },
    );
  }

  createLlmCredential(provider: string, apiKey: string): Promise<LlmCredentialResponse> {
    return this.request<LlmCredentialResponse>("/llm/credentials", {
      method: "POST",
      body: JSON.stringify({ provider, apiKey }),
    });
  }

  testLlmConfig(config: LlmProviderConfig): Promise<LlmConfigTestResponse> {
    return this.request<LlmConfigTestResponse>("/llm/test", {
      method: "POST",
      body: JSON.stringify({ config }),
    });
  }

  getLlmConfigFile(): Promise<LlmConfigFileResponse> {
    return this.request<LlmConfigFileResponse>("/llm/config-file");
  }

  updateLlmConfigFile(llmConfig: LlmRuntimeConfig): Promise<LlmConfigFileResponse> {
    return this.request<LlmConfigFileResponse>("/llm/config-file", {
      method: "PATCH",
      body: JSON.stringify(llmConfig),
    });
  }

  updatePipelineLlmConfig(pipelineId: string, llmConfig: LlmRuntimeConfig): Promise<LlmRuntimeConfig> {
    return this.request<LlmRuntimeConfig>(`/pipelines/${encodeURIComponent(pipelineId)}/llm-config`, {
      method: "PATCH",
      body: JSON.stringify(llmConfig),
    });
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    /*
     * 前端只依赖控制平面的 REST 契约，不直接感知 Temporal 或数据库。
     * 这里统一补齐 JSON 头、解析错误体，并把 HTTP 错误转换成带 status/body 的异常。
     * UI 层因此可以稳定展示“后端返回了什么”，而不是只能显示浏览器 fetch 的模糊失败信息。
     */
    const response = await this.fetchImpl(`${this.baseUrl}${path}`, {
      ...init,
      headers: {
        accept: "application/json",
        ...(init.body ? { "content-type": "application/json; charset=utf-8" } : {}),
        ...init.headers,
      },
    });

    const body = await readJsonBody(response);
    if (!response.ok) {
      const message = extractErrorMessage(body) || `Control plane request failed: ${response.status}`;
      throw new PipelineApiError(message, response.status, body);
    }
    return body as T;
  }
}

async function readJsonBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) {
    return {};
  }
  try {
    return JSON.parse(text);
  } catch {
    return { raw: text };
  }
}

function extractErrorMessage(body: unknown): string | null {
  if (body && typeof body === "object" && "message" in body) {
    return String((body as { message?: unknown }).message);
  }
  return null;
}
