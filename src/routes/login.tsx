import { createFileRoute } from "@tanstack/react-router";
import { LoginPage } from "@/components/vivesec/AuthPages";

export const Route = createFileRoute("/login")({
  head: () => ({
    meta: [{ title: "ViVeSec AI Box" }, { name: "robots", content: "noindex" }],
  }),
  component: LoginPage,
});
