import express, { Request, Response } from "express";
import cors from "cors";
import { randomUUID } from "node:crypto";

export interface ModifyRequest {
  fileName: string;
  lineNumber: number;
  userInstruction: string;
}

export interface AcceptedModifyResponse {
  requestId: string;
  status: "ACCEPTED";
  fileName: string;
  message: string;
}

export interface ErrorResponse {
  code: string;
  message: string;
}

function isModifyRequest(body: unknown): body is ModifyRequest {
  if (!body || typeof body !== "object") {
    return false;
  }

  const candidate = body as Partial<ModifyRequest>;
  return (
    typeof candidate.fileName === "string" &&
    candidate.fileName.length > 0 &&
    typeof candidate.lineNumber === "number" &&
    Number.isInteger(candidate.lineNumber) &&
    candidate.lineNumber > 0 &&
    typeof candidate.userInstruction === "string" &&
    candidate.userInstruction.length > 0
  );
}

export function createApp() {
  const app = express();

  app.use(cors());
  app.use(express.json());

  app.post("/api/sandbox/modify", (request: Request, response: Response<AcceptedModifyResponse | ErrorResponse>) => {
    if (!isModifyRequest(request.body)) {
      response.status(400).json({
        code: "INVALID_REQUEST",
        message: "Request body must include fileName, lineNumber, and userInstruction.",
      });
      return;
    }

    response.status(202).json({
      requestId: randomUUID(),
      status: "ACCEPTED",
      fileName: request.body.fileName,
      message: "Modification request accepted. AST lookup, code generation, and file writing will be added later.",
    });
  });

  return app;
}
