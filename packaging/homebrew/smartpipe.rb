# Formula for the personal tap (prabal-rje/homebrew-tap). Installs the PyPI
# distribution `smartpipe-cli` into a formula-owned virtualenv pinned to
# python@3.13. A personal tap may use binary wheels, so dependencies resolve
# from PyPI at install time instead of being vendored as resource blocks —
# the url + sha256 pin the exact smartpipe release.
#
# Per-release bump (see RELEASING.md "4. Homebrew tap"): update `url` to the
# new sdist and refresh `sha256`.
class Smartpipe < Formula
  include Language::Python::Virtualenv

  desc "Semantic pipes and queries for your terminal"
  homepage "https://prabal-rje.github.io/smartpipe"
  url "https://files.pythonhosted.org/packages/source/s/smartpipe-cli/smartpipe_cli-1.3.1.tar.gz"
  sha256 "REPLACE_WITH_SDIST_SHA256"  # shasum -a 256 smartpipe_cli-1.3.1.tar.gz
  license "MIT"

  depends_on "python@3.13"

  def install
    venv = virtualenv_create(libexec, "python3.13")
    # The downloaded, checksummed sdist is what gets installed; its
    # dependencies come from PyPI with binary wheels (fine in a personal tap).
    system libexec/"bin/python", "-m", "pip", "install", buildpath.to_s
    bin.install_symlink libexec/"bin/smartpipe"

    # Click's completion machinery: _SMARTPIPE_COMPLETE={shell}_source emits
    # the completion script; Homebrew drops each into its completion dir.
    generate_completions_from_executable(bin/"smartpipe", shell_parameter_format: :click)
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/smartpipe --version")
  end
end
