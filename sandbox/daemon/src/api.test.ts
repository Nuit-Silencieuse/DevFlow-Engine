import assert from "node:assert/strict";
import { AddressInfo } from "node:net";
import { createApp } from "./api";

async function withServer(run: (baseUrl: string) => Promise<void>): Promise<void> {
  const app = createApp();
  const server = app.listen(0);

  await new Promise<void>((resolve) => server.once("listening", resolve));
  const address = server.address() as AddressInfo;

  try {
    await run(`http://127.0.0.1:${address.port}`);
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close((error?: Error) => (error ? reject(error) : resolve()));
    });
  }
}

async function testModifyRequestAccepted(): Promise<void> {
  await withServer(async (baseUrl) => {
    const response = await fetch(`${baseUrl}/api/sandbox/modify`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        fileName: "/src/components/Header.tsx",
        lineNumber: 42,
        userInstruction: "把按钮改成红色，并修改文案为立即体验",
      }),
    });

    assert.equal(response.status, 202);
    const body = await response.json();
    assert.equal(body.status, "ACCEPTED");
    assert.equal(body.fileName, "/src/components/Header.tsx");
    assert.ok(body.requestId);
  });
}

async function testIncompleteRequestRejected(): Promise<void> {
  await withServer(async (baseUrl) => {
    const response = await fetch(`${baseUrl}/api/sandbox/modify`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ fileName: "/src/App.tsx" }),
    });

    assert.equal(response.status, 400);
    const body = await response.json();
    assert.equal(body.code, "INVALID_REQUEST");
  });
}

async function main(): Promise<void> {
  await testModifyRequestAccepted();
  await testIncompleteRequestRejected();
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
