# Connect your own agent

Slate does not install a model or copy an agent into the board. It connects to the agent you already run. Your agent keeps its own tools, credentials, instructions and memory.

## Choose what you want

Open **Agents → Add my agent**.

- **Let Slate start my agent:** connect the computer that runs your agent. Slate can give it work while the local connection command is running.
- **Let my agent use Slate:** give an already-running agent access through MCP or the API. This alone does not let Slate start that agent.
- **I will pass tasks to it myself:** copy task instructions when needed. No background connection is promised.

Choose only the lists the agent needs. New accounts created by this screen do not automatically get the rest of the workspace. You can change access later.

## Connect the agent's computer

Install the matching runner package from your copy of Slate as described in the root README. On the **agent's computer**, run the command shown in the dialog. The command includes your server address, not a secret. It asks privately for the short-lived code shown on the board.

Select your local runtime, its existing profile or address, and the directory where it should work. The command saves a separate local connection; it does not rewrite all your agent profiles. Runtime credentials stay on that computer. Remote Slate servers require HTTPS; local loopback development can use HTTP.

Once paired, the local command prints the connection identifier and the commands for checking and starting it. A paired credential means **Connected**, not **Ready**. Run the explicit connection test when you want to prove that the chosen agent can answer. The test is a harmless labelled round trip, not a real task from your lists. It may use your configured model subscription/provider; it is not run repeatedly in the background.

Start that connection's runner to receive work. Keep it running on the agent's computer. Slate does not register a startup service or start a model for you. For a daemon/service, use your operating system's normal supervisor and the documented runner command.

## What the status means

**Waiting for your computer** means the code has not been exchanged. **Connected; test not run** means the credential works but the agent has not passed its test. **Ready** means the explicit test passed and the connection is active. **Offline** means the connection has stopped checking in; it does not prove a remote job was cancelled. **Needs attention** includes a plain-language reason.

A successful test remains recorded while the connection stays healthy and its local setup is unchanged. Regular health checks do not invoke the model. A changed setup or failed runtime check requires another explicit test. Private connection details are available only to the agent itself and workspace managers; missing access is not reported as an operational failure.

Disconnecting revokes that connection's Slate credential. It does not delete the agent's memory, uninstall its runtime, or claim that an already-running remote job stopped.

## Permissions and approvals

Slate's list restrictions govern what the agent can read or change **in Slate**. They are not a sandbox for the agent's computer. Select a runtime/profile with the tools and filesystem access appropriate to the project.

When the runtime asks for permission, use its native approval control. A casual "yes" on the card is not converted into permission to execute a command. Slate never invents owner authorization when an approval endpoint is unavailable.

## If setup fails

Use the printed `doctor` command. Without the explicit test option it checks configuration and connectivity without starting a model turn. It does not print credentials. An expired or already-used code needs a fresh code from the board. If credential saving fails after exchange, disconnect the issued connection before creating a replacement; never keep retrying an already-consumed code.

Custom commands must follow the runner protocol. They can provide an explicit harmless probe command. An unsupported probe is reported honestly, not treated as proof the agent works. See [the runner guide](agents/runner.md) and [the Hermes adapter](../integrations/hermes/README.md) for supported capabilities and limitations.
