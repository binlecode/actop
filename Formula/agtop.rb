class Agtop < Formula
  include Language::Python::Virtualenv

  desc "Performance monitoring CLI tool for Apple Silicon"
  homepage "https://github.com/binlecode/agtop"
  url "https://github.com/binlecode/agtop/archive/refs/tags/v0.8.1.tar.gz"
  sha256 "36f6b1c071631e8ebb0f39292d743cefae98ff0fcb997b76578e900f0686ac31"
  license "MIT"

  bottle do
    root_url "https://github.com/binlecode/agtop/releases/download/v0.8.1"
    rebuild 16
    sha256 cellar: :any_skip_relocation, arm64_tahoe:   "6efe7a21d27e47e409dda3592ef20bcbc1f304d18d72a9f773fc9cb97b4edaa5"
    sha256 cellar: :any_skip_relocation, arm64_sequoia: "750532480b30197a315b20e5b00904cad0e01090a22436d5c57abde3c7ada2d6"
    sha256 cellar: :any_skip_relocation, arm64_sonoma:  "cfc6af4e252bab17e951272413a48b58354bf88b48be49e99f5e588446445baf"
  end

  depends_on "python@3.13"

  resource "linkify-it-py" do
    url "https://files.pythonhosted.org/packages/2e/c9/06ea13676ef354f0af6169587ae292d3e2406e212876a413bf9eece4eb23/linkify_it_py-2.1.0.tar.gz"
    sha256 "43360231720999c10e9328dc3691160e27a718e280673d444c38d7d3aaa3b98b"
  end

  resource "markdown-it-py" do
    url "https://files.pythonhosted.org/packages/5b/f5/4ec618ed16cc4f8fb3b701563655a69816155e79e24a17b651541804721d/markdown_it_py-4.0.0.tar.gz"
    sha256 "cb0a2b4aa34f932c007117b194e945bd74e0ec24133ceb5bac59009cda1cb9f3"
  end

  resource "mdit-py-plugins" do
    url "https://files.pythonhosted.org/packages/b2/fd/a756d36c0bfba5f6e39a1cdbdbfdd448dc02692467d83816dff4592a1ebc/mdit_py_plugins-0.5.0.tar.gz"
    sha256 "f4918cb50119f50446560513a8e311d574ff6aaed72606ddae6d35716fe809c6"
  end

  resource "mdurl" do
    url "https://files.pythonhosted.org/packages/d6/54/cfe61301667036ec958cb99bd3efefba235e65cdeb9c84d24a8293ba1d90/mdurl-0.1.2.tar.gz"
    sha256 "bb413d29f5eea38f31dd4754dd7377d4465116fb207585f97bf925588687c1ba"
  end

  resource "platformdirs" do
    url "https://files.pythonhosted.org/packages/1b/04/fea538adf7dbbd6d186f551d595961e564a3b6715bdf276b477460858672/platformdirs-4.9.2.tar.gz"
    sha256 "9a33809944b9db043ad67ca0db94b14bf452cc6aeaac46a88ea55b26e2e9d291"
  end

  resource "pygments" do
    url "https://files.pythonhosted.org/packages/b0/77/a5b8c569bf593b0140bde72ea885a803b82086995367bf2037de0159d924/pygments-2.19.2.tar.gz"
    sha256 "636cb2477cec7f8952536970bc533bc43743542f70392ae026374600add5b887"
  end

  resource "rich" do
    url "https://files.pythonhosted.org/packages/b3/c6/f3b320c27991c46f43ee9d856302c70dc2d0fb2dba4842ff739d5f46b393/rich-14.3.3.tar.gz"
    sha256 "b8daa0b9e4eef54dd8cf7c86c03713f53241884e814f4e2f5fb342fe520f639b"
  end

  resource "textual" do
    url "https://files.pythonhosted.org/packages/be/08/c6bcb1e3c4c9528ec9049f4ac685afdafc72866664270f0deb416ccbba2a/textual-8.0.2.tar.gz"
    sha256 "7b342f3ee9a5f2f1bd42d7b598cae00ff1275da68536769510db4b7fe8cabf5d"
  end

  resource "typing-extensions" do
    url "https://files.pythonhosted.org/packages/72/94/1a15dd82efb362ac84269196e94cf00f187f7ed21c242792a923cdb1c61f/typing_extensions-4.15.0.tar.gz"
    sha256 "0cea48d173cc12fa28ecabc3b837ea3cf6f38c6d1136f85cbaaf598984861466"
  end

  resource "uc-micro-py" do
    url "https://files.pythonhosted.org/packages/78/67/9a363818028526e2d4579334460df777115bdec1bb77c08f9db88f6389f2/uc_micro_py-2.0.0.tar.gz"
    sha256 "c53691e495c8db60e16ffc4861a35469b0ba0821fe409a8a7a0a71864d33a811"
  end

  def install
    virtualenv_install_with_resources(using: "python@3.13")
  end

  test do
    output = shell_output("#{bin}/agtop --help")
    assert_match "Performance monitoring CLI tool for Apple Silicon", output
  end
end
