"""Specialist agents. Agents are isolated: they read an AgentContext and return an
AgentResult. They never write to the database, the filesystem or each other;
all coordination goes through the Brain."""
