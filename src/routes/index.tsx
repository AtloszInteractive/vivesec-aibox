import { createFileRoute } from "@tanstack/react-router";
import { ViveSecApp } from "@/components/vivesec/ViveSecApp";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "ViVeSec AI Platform" },
      { name: "description", content: "Premium offline-first enterprise AI workspace with encrypted chat, agents, and secure document drive." },
      { property: "og:title", content: "ViVeSec AI Platform" },
      { property: "og:description", content: "Premium offline-first enterprise AI workspace." },
    ],
  }),
  component: Index,
});

function Index() {
  return <ViveSecApp />;
}
