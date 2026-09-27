# Vaibify website

The standalone homepage for `vaibify.com`. Only `public/` is published.
The site uses HTML, CSS, and local assets; there is no build step, JavaScript,
application server, analytics, or credential configuration in the page.
The existing documentation and its GitHub Pages deployment stay separate.
All documentation links currently point to `RoryBarnes.github.io/vaibify`.

The homepage shows the real dashboard image from
`docs/vaibify_screenshot.png`, copied unchanged to
`public/assets/vaibifyDashboard.png`. It is a recorded research session,
not live project state; the caption identifies the pending verification.
Replace the copy when updating the screenshot and check its caption against
what the new image actually shows. The logo uses the existing application
branding. The favicon is an unchanged copy of
`vaibify/gui/static/favicon.png`, the icon used by the application.

## Preview locally

From this directory:

```sh
python -m http.server 8765 --bind 127.0.0.1 --directory public
```

Open <http://127.0.0.1:8765>. This previews the layout; Python's static
server does not apply Cloudflare's headers or custom 404 behavior.
To exercise those with Cloudflare's local runtime, use Node.js and:

```sh
npx wrangler@4.141.0 pages dev --ip 127.0.0.1 --port 8788
```

Open <http://127.0.0.1:8788>. Local preview does not require a Cloudflare
account or login. The deployment directory is declared in `wrangler.toml`.

## Connect Cloudflare Pages

Use **Pages with Git integration**. Cloudflare also offers Workers;
this configuration targets Pages. Git integration provides deployments
from the repository without adding a GitHub Actions workflow.

1. Create a Cloudflare account, or sign in to an existing account.
2. Under **Workers & Pages**, create an application, choose **Pages**,
   and import an existing Git repository.
3. Authorize Cloudflare's GitHub application for `RoryBarnes/vaibify`.
   Access to this repository is sufficient; access to all repositories
   is unnecessary. Complete authentication in your own browser.
4. Use these settings after the website changes reach the production branch:

   | Setting                | Value                                         |
   | ---------------------- | --------------------------------------------- |
   | Project name           | `vaibify-website` (or another available name) |
   | Production branch      | `main`                                        |
   | Root directory         | `website`                                     |
   | Framework preset       | None                                          |
   | Build command          | `exit 0`                                      |
   | Build output directory | `public`                                      |

   The output path is relative to the `website` root. Never publish the
   repository root: the publishable files are exclusively in `public/`.
   If choosing another project name, update `name` in `wrangler.toml` to match.

5. Deploy and review the assigned `*.pages.dev` address before connecting
   `vaibify.com`. Cloudflare assigns the address; do not assume a particular
   project name or address is available.
6. In the project's build watch paths, include `website/*` so unrelated
   application and documentation commits do not trigger website builds.
   Branch previews can be enabled for reviewing future website changes.

For a first hosted review before merging, push `feat/website` and choose it
as the initial production branch instead of `main`. Keep that deployment
on its assigned `pages.dev` address. After merging, change the production
branch to `main`, deploy it, and then connect the domain. A production
branch called `main` cannot serve this site until it contains `website/`.

Do not create a Direct Upload project as the first step if you want this
Git workflow: Cloudflare does not allow converting a Direct Upload project
to Git integration. There is no need to send anyone a password or API token
for the Git integration setup.

## Connect vaibify.com

For an apex domain such as `vaibify.com`, Pages requires the domain's DNS
zone to be on Cloudflare. The domain can remain registered with its current
registrar; changing DNS hosting does not require transferring registration.

1. Add `vaibify.com` to your Cloudflare account and review the imported DNS
   records against your current provider, including mail and verification
   records. Keep the existing services' records when moving DNS.
2. Follow Cloudflare's zone onboarding instructions, including any DNSSEC
   steps, and set the assigned nameservers at your registrar. Wait until
   Cloudflare reports the zone active.
3. In the Pages project, add `vaibify.com` under **Custom domains** and
   follow the DNS setup. Add `www.vaibify.com` there too if it should serve
   the site. Configure a Cloudflare redirect from `www` to the apex, retaining
   the path and query string, to keep one canonical address.
4. Check HTTPS, the homepage, a missing page, and documentation links on the
   live domain. Domain attachment and DNS changes are a separate launch step.

No `docs.vaibify.com` record or documentation migration is part of this setup.
The homepage's canonical URL and sitemap point to `https://vaibify.com/`.
Cloudflare marks branch previews `noindex` by default; the project's initial
production `pages.dev` deployment is distinct from a branch preview. Once
the custom domain is live, use Cloudflare's documented redirect for that
production `pages.dev` hostname, preserving branch preview access.

## Review and validation

Before publishing, check desktop and mobile layouts, keyboard focus and
the skip link, in-page navigation, image loading, documentation destinations,
and a nonexistent URL. Check the actual Pages runtime to verify `_headers`
and that `404.html` returns HTTP 404 rather than the homepage with HTTP 200.

The content security policy permits only local images and styles. Introducing
scripts, third-party fonts, analytics, or forms requires reviewing that
policy along with the new dependency. The site currently makes no external
requests until the visitor follows an external link.

Run the repository's spelling check on the new site text:

```sh
python tools/checkAmericanSpelling.py website/README.md website/public/index.html website/public/404.html
```

Run that command from the repository root. Python and application JavaScript
are unchanged by this website, and the application browser suite does not
exercise this separate static site.

## Cloudflare references

- [Deploy static HTML](https://developers.cloudflare.com/pages/framework-guides/deploy-anything/)
- [Git integration](https://developers.cloudflare.com/pages/get-started/git-integration/)
- [Build watch paths](https://developers.cloudflare.com/pages/configuration/build-watch-paths/)
- [Custom domains](https://developers.cloudflare.com/pages/configuration/custom-domains/)
- [Wrangler configuration](https://developers.cloudflare.com/pages/functions/wrangler-configuration/)
- [Custom headers](https://developers.cloudflare.com/pages/configuration/headers/)
- [Preview deployments](https://developers.cloudflare.com/pages/configuration/preview-deployments/)
- [Redirect a production pages.dev address](https://developers.cloudflare.com/pages/how-to/redirect-to-custom-domain/)
