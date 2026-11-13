import { createFileRoute } from "@tanstack/react-router";
import { PasswordPage } from "@/components/vivesec/AuthPages";

export const Route = createFileRoute("/password")({
  head: () => ({
    meta: [{ title: "ViVeSec AI Box" }, { name: "robots", content: "noindex" }],
  }),
  component: PasswordPage,
});
