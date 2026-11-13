import { createFileRoute } from "@tanstack/react-router";
import { AdminPage } from "@/components/vivesec/admin/AdminPage";

export const Route = createFileRoute("/admin")({
  head: () => ({
    meta: [{ title: "ViVeSec AI Box" }, { name: "robots", content: "noindex" }],
  }),
  component: AdminPage,
});
