/*
 * 文件说明：使用真实浏览器回归统一工作台的会话、提问、引用、文件、重试和移动端流程。
 * 只允许运行在带测试标识的模拟服务上，防止把测试写入提交给真实 Agent。
 */

/* Run against tests.workspace_server only; all tasks use isolated test doubles. */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

(async () => {
  const output = path.resolve(
    process.env.PAPERLENS_TEST_OUTPUT || "data/frontend-checks",
  );
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ channel: process.platform === "win32" ? "msedge" : undefined, headless: true })
    .catch(() => chromium.launch({ headless: true }));
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    acceptDownloads: true,
  });
  const page = await context.newPage();
  const base = process.env.PAPERLENS_TEST_URL || "http://127.0.0.1:18091";
  const errors = [],
    passed = [];
  page.on("pageerror", (error) => errors.push(error.message));
  async function check(name, fn) {
    await fn();
    passed.push(name);
    console.log("PASS", name);
  }
  async function send(question) {
    await page.locator("#question").fill(question);
    await page.locator("#send-button").click();
  }
  async function waitDone(count) {
    await page.waitForFunction(
      (n) =>
        document.querySelectorAll(".message.assistant").length >= n &&
        !document.querySelector(".task-card:not(.failed)"),
      count,
    );
  }
  async function storedSession() {
    return page.evaluate(() => {
      const user = JSON.parse(localStorage.getItem("paperlens.user"));
      return JSON.parse(localStorage.getItem("paperlens.preferences." + user))
        .session;
    });
  }
  try {
    const marker = await page.request.get(base + "/__workspace_test__");
    assert(
      marker.ok(),
      "Start tests.workspace_server; do not run these writes against the real Agent",
    );
    assert.equal((await marker.json()).fixture, "paperlens-e2e");
    await page.goto(base);
    await page
      .locator('#paper-select option[value="paper-a"]')
      .waitFor({ state: "attached" });
    await page.screenshot({ path: path.join(output, "01-welcome.png") });
    await check("new workspace, papers, and safe empty state", async () => {
      assert(await page.locator("#empty-state").isVisible());
      assert(await page.locator("#send-button").isDisabled());
      assert.equal(await page.locator(".session-item").count(), 0);
      await page.locator("#paper-select").selectOption("paper-a");
      assert.match(
        await page.locator("#selected-paper").innerText(),
        /Learning from Evidence/,
      );
    });
    await check(
      "lost acknowledgement retries the same run, persists mode and escapes model HTML",
      async () => {
        let lost = false;
        await page.route("**/sessions/*/runs?*", async (route) => {
          if (!lost && route.request().method() === "POST") {
            lost = true;
            await route.fetch();
            await route.abort("failed");
          } else await route.continue();
        });
        await send("记住代号 BLUE");
        await page.waitForFunction(
          () =>
            document
              .querySelector("#toast")
              .textContent.includes("网络连接中断") &&
            !document.querySelector("#send-button").disabled,
        );
        await page.locator("#send-button").click();
        await waitDone(1);
        assert(await page.locator(".message.assistant table").count());
        assert.equal(await page.evaluate(() => window.injected), undefined);
        assert.match(
          await page.locator(".message.assistant").innerText(),
          /<script>/,
        );
        const user = await page.evaluate(() =>
          JSON.parse(localStorage.getItem("paperlens.user")),
        );
        const sid = await storedSession();
        const runs = await (
          await page.request.get(
            `${base}/sessions/${sid}/runs?user_id=${encodeURIComponent(user)}`,
          )
        ).json();
        assert.equal(runs.runs.length, 1);
        assert.equal(runs.runs[0].mode, "qa");
        await page.screenshot({
          path: path.join(output, "02-conversation.png"),
          animations: "disabled",
        });
      },
    );
    const firstId = await storedSession();
    const originalUser = await page.evaluate(() =>
      JSON.parse(localStorage.getItem("paperlens.user")),
    );
    await check(
      "citation opens real excerpt, file preview and data download",
      async () => {
        await page.locator(".source-button").first().click();
        assert(
          (await page.locator("#evidence-0").getAttribute("open")) !== null,
        );
        assert.match(
          await page.locator("#evidence-0").innerText(),
          /controlled comparison/,
        );
        await page.locator("#tab-files").click();
        await page
          .locator(".file-card")
          .filter({ hasText: "presentation.md" })
          .locator("[data-preview]")
          .click();
        await page.locator("#preview-content h1").waitFor();
        await page.locator("#preview-dialog [data-close-dialog]").click();
        const promise = page.waitForEvent("download");
        await page
          .locator(".file-card")
          .filter({ hasText: "values.csv" })
          .locator("a")
          .click();
        const download = await promise;
        assert.equal(download.suggestedFilename(), "values.csv");
        await download.saveAs(
          path.join(output, "downloaded-values.csv"),
        );
        assert.equal(fs.readFileSync(path.join(output, "downloaded-values.csv"), "utf8").replace(/\r/g, ""), "value\n1\n");
      },
    );
    await check(
      "live SSE progress, duplicate click guard and refresh while running",
      async () => {
        await page.locator('[data-compose-mode="agent"]').click();
        await send("slow 保持上下文");
        await page.locator(".task-card").waitFor();
        assert(await page.locator("#send-button").isDisabled());
        await page.locator("#tab-process").click();
        await page.waitForFunction(
          () => document.querySelectorAll(".event-item").length >= 3,
        );
        await page.reload();
        await page.locator(".task-card").waitFor();
        assert.equal(await storedSession(), firstId);
        await waitDone(2);
        await page.locator("#tab-process").click();
        await page.waitForFunction(() =>
          document
            .querySelector("#event-list")
            .textContent.includes("回答与文件已保存"),
        );
        const data = await (
          await page.request.get(
            `${base}/sessions/${firstId}/runs?user_id=${encodeURIComponent(originalUser)}`,
          )
        ).json();
        assert.equal(data.runs.length, 2);
        assert.equal(data.runs[0].mode, "agent");
        await page.screenshot({
          path: path.join(output, "03-progress.png"),
          animations: "disabled",
        });
      },
    );
    await check("context survives refresh and follow-up", async () => {
      await send("代号是什么");
      await waitDone(3);
      assert.match(
        await page.locator(".message.assistant").last().innerText(),
        /代号是 BLUE/,
      );
    });
    await check(
      "new conversation isolates context and switching restores old conversation",
      async () => {
        await page.locator("#new-chat").click();
        await page.locator("#paper-select").selectOption("paper-b");
        await send("代号是什么");
        await waitDone(1);
        assert.match(
          await page.locator(".message.assistant").last().innerText(),
          /没有先前代号/,
        );
        assert.notEqual(await storedSession(), firstId);
        await page.locator(`[data-session="${firstId}"]`).click();
        await waitDone(3);
        assert.match(
          await page.locator(".message.assistant").last().innerText(),
          /代号是 BLUE/,
        );
      },
    );
    await check(
      "rename persists and failure can be explicitly retried",
      async () => {
        await page.locator("#rename-session").click();
        await page.locator("#rename-input").fill("端到端研读记录");
        await page.locator('#rename-form button[type="submit"]').click();
        await page.waitForFunction(
          () =>
            document.querySelector("#session-title").textContent ===
            "端到端研读记录",
        );
        await send("fail");
        await page.locator(".task-card.failed").waitFor();
        assert.match(
          await page.locator(".task-card.failed").innerText(),
          /测试错误/,
        );
        await page.locator("[data-retry]").click();
        await waitDone(4);
        assert.match(
          await page.locator(".message.assistant").last().innerText(),
          /retry passed/,
        );
      },
    );
    await check(
      "upload goes through processing to ready, then selects the new paper",
      async () => {
        await page.locator("#new-chat").click();
        await page
          .locator("#pdf-file")
          .setInputFiles({
            name: "browser-paper.pdf",
            mimeType: "application/pdf",
            buffer: Buffer.from("%PDF-1.7\nbrowser fixture"),
          });
        await page.waitForFunction(() =>
          document
            .querySelector("#upload-status")
            .textContent.includes("解析完成"),
        );
        assert.equal(
          await page.locator("#paper-select").inputValue(),
          "new-paper",
        );
        await page.locator("#library-nav").click();
        assert.equal(await page.locator(".library-card").count(), 3);
        await page.screenshot({ path: path.join(output, "04-library.png") });
        await page.locator('[data-use-paper="paper-a"]').click();
      },
    );
    await check(
      "record identity restores history without leaking drafts",
      async () => {
        await page.locator("#question").fill("private draft");
        await page.locator("#identity-button").click();
        await page.locator("#identity-input").fill("another-browser-user");
        await page.locator('#identity-form button[type="submit"]').click();
        await page.waitForFunction(
          () => document.querySelectorAll(".session-item").length === 0,
        );
        assert.equal(await page.locator("#question").inputValue(), "");
        await page.locator("#identity-button").click();
        await page.locator("#identity-input").fill(originalUser);
        await page.locator('#identity-form button[type="submit"]').click();
        await page.locator(`[data-session="${firstId}"]`).waitFor();
        await page.locator(`[data-session="${firstId}"]`).click();
        await waitDone(4);
      },
    );
    await check(
      "mobile navigation, workspace and no horizontal page overflow",
      async () => {
        await page.setViewportSize({ width: 390, height: 844 });
        assert(await page.locator("#open-nav").isVisible());
        await page.locator("#open-nav").click();
        assert(
          await page
            .locator("#sidebar")
            .evaluate((el) => el.classList.contains("open")),
        );
        await page.locator("#chat-nav").click();
        await page.locator("#open-workspace").click();
        await page.locator("#tab-files").click();
        await page.screenshot({
          path: path.join(output, "05-mobile-files.png"),
        });
        await page.locator("#close-workspace").click();
        await page.waitForTimeout(250);
        assert(
          await page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth + 1,
          ),
        );
        await page.screenshot({
          path: path.join(output, "06-mobile-chat.png"),
        });
      },
    );
    assert.deepEqual(errors, []);
    fs.writeFileSync(
      path.join(output, "result.json"),
      JSON.stringify({ ok: true, passed, pageErrors: errors }, null, 2),
    );
    console.log("ALL PASS", passed.length);
  } catch (error) {
    await page
      .screenshot({ path: path.join(output, "failure.png") })
      .catch(() => {});
    fs.writeFileSync(
      path.join(output, "result.json"),
      JSON.stringify(
        { ok: false, passed, error: error.stack, pageErrors: errors },
        null,
        2,
      ),
    );
    throw error;
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
