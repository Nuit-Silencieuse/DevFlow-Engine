import { createApp } from "./api";

const port = Number(process.env.PORT ?? 8080);
const app = createApp();

app.listen(port, () => {
  console.log(`DevFlow sandbox daemon listening on http://localhost:${port}`);
});
